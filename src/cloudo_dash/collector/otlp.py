"""Приём официальной телеметрии Claude Code по OTLP (веха E, ТЗ §2).

Claude Code умеет отдавать метрики и события в OTLP; включается это
переменными окружения (`cdash otel` печатает какими). Спецификация сверена с
https://code.claude.com/docs/en/monitoring-usage 14 августа 2026.

Из трёх кодировок OTLP берётся `http/json`: она разбирается штатным json без
grpcio и protobuf, а приёмник живёт прямо в приложении `cdash serve` и не
занимает второй порт. Тела приходят на `/otlp/v1/metrics` и `/otlp/v1/logs`.

Телеметрия — второй канал, а не замена парсеру: он в бете у Anthropic, поэтому
данные ложатся в свои таблицы (`otel_metrics`, `otel_events`), а сходятся ли
два канала — проверяет сверка E3.

Разбор такой же терпимый, как у парсера транскриптов: незнакомые поля
игнорируются, непонятный кусок посылки считается потерянным (`dropped`) и не
роняет остальную пачку — иначе смена формата обрывала бы приём целиком.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson

log = logging.getLogger(__name__)

#: Формат времени общий с метриками из транскриптов: UTC с Z, сравнивается строкой.
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

#: Префикс имён Claude Code: `claude_code.token.usage`, событие `claude_code.api_request`.
PREFIX = "claude_code."

#: Атрибуты, которые в БД не кладутся. Они одинаковы у каждой точки одной
#: машины и на расчёты не влияют, зато почта и идентификаторы аккаунта копией
#: в каждой строке — лишние данные о человеке, а не о расходе (ТЗ §7).
SKIPPED_ATTRS = frozenset(
    {
        "user.email",
        "user.id",
        "user.account_id",
        "user.account_uuid",
        "user.groups",
        "organization.id",
        "identity.source",
        "host.arch",
        "os.type",
        "os.version",
        "service.name",
    }
)

#: Сигналы OTLP, которые приёмник принимает. Трассы (бета `ENHANCED_TELEMETRY`)
#: подтверждаются, но не разбираются: считать по ним нечего, а 404 заставил бы
#: экспортёр долбиться повторами.
SIGNALS = ("metrics", "logs", "traces")


@dataclass(frozen=True)
class MetricPoint:
    """Точка метрики: имя, окно, значение и все атрибуты как есть."""

    name: str
    ts: str
    start_ts: str | None
    session_id: str | None
    model: str | None
    kind: str | None  # атрибут type: input | output | cacheRead | cacheCreation | added | ...
    value: float
    attrs: dict[str, Any]

    @property
    def key(self) -> str:
        """Отпечаток точки: в одном окне на набор атрибутов она ровно одна."""
        return _fingerprint(self.name, self.start_ts, self.ts, _canonical(self.attrs))


@dataclass(frozen=True)
class EventRecord:
    """Событие телеметрии (OTLP log record) с плоским набором атрибутов."""

    name: str
    ts: str
    session_id: str | None
    attrs: dict[str, Any]

    @property
    def key(self) -> str:
        """Отпечаток события.

        `event.sequence` монотонно растёт внутри сессии, поэтому вместе с ней и
        временем однозначно называет событие. Без счётчика остаётся отпечаток
        по атрибутам — он тоже гасит повтор той же посылки.
        """
        sequence = self.attrs.get("event.sequence")
        if self.session_id and sequence is not None:
            return _fingerprint(self.session_id, sequence, self.name, self.ts)
        return _fingerprint(self.name, self.ts, _canonical(self.attrs))


def decode(body: bytes, content_encoding: str | None = None) -> Any:
    """Разобрать тело запроса OTLP/JSON, при необходимости распаковав gzip."""
    if content_encoding and "gzip" in content_encoding.lower():
        body = gzip.decompress(body)
    return orjson.loads(body)


def ingest(conn: sqlite3.Connection, signal: str, payload: Any) -> dict[str, int]:
    """Разобрать посылку и записать её; вернуть счётчики для ответа и логов."""
    seen = 0
    stored = 0
    dropped = 0
    if signal == "metrics":
        points, dropped = parse_metrics(payload)
        seen = len(points)
        stored = store_metrics(conn, points)
    elif signal == "logs":
        events, dropped = parse_logs(payload)
        seen = len(events)
        stored = store_events(conn, events)
    note_ingest(conn, signal, stored=stored, dropped=dropped)
    return {"seen": seen, "stored": stored, "dropped": dropped}


def parse_metrics(payload: Any) -> tuple[list[MetricPoint], int]:
    """Разобрать `ExportMetricsServiceRequest`; вернуть точки и число потерь."""
    points: list[MetricPoint] = []
    dropped = 0
    for resource in _items(payload, "resourceMetrics", "resource_metrics"):
        base = _resource_attrs(resource)
        for scope in _items(resource, "scopeMetrics", "scope_metrics"):
            for metric in _items(scope, "metrics"):
                found, lost = _metric_points(metric, base)
                points.extend(found)
                dropped += lost
    return points, dropped


def parse_logs(payload: Any) -> tuple[list[EventRecord], int]:
    """Разобрать `ExportLogsServiceRequest`: события Claude Code едут логами."""
    events: list[EventRecord] = []
    dropped = 0
    for resource in _items(payload, "resourceLogs", "resource_logs"):
        base = _resource_attrs(resource)
        for scope in _items(resource, "scopeLogs", "scope_logs"):
            for record in _items(scope, "logRecords", "log_records"):
                event = _event(record, base)
                if event is None:
                    dropped += 1
                else:
                    events.append(event)
    return events, dropped


def store_metrics(conn: sqlite3.Connection, points: list[MetricPoint]) -> int:
    """Записать точки, погасив повторы посылки; вернуть число новых строк."""
    rows = [
        (
            point.key,
            point.name,
            point.ts,
            point.start_ts,
            point.session_id,
            point.model,
            point.kind,
            point.value,
            _canonical(point.attrs),
        )
        for point in points
    ]
    return _insert(
        conn,
        "INSERT OR IGNORE INTO otel_metrics"
        " (key, name, ts, start_ts, session_id, model, kind, value, attrs)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def store_events(conn: sqlite3.Connection, events: list[EventRecord]) -> int:
    rows = [
        (event.key, event.name, event.ts, event.session_id, _canonical(event.attrs))
        for event in events
    ]
    return _insert(
        conn,
        "INSERT OR IGNORE INTO otel_events (key, name, ts, session_id, attrs)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )


def note_ingest(conn: sqlite3.Connection, signal: str, *, stored: int, dropped: int) -> None:
    """Отметить приём посылки: по этим счётчикам видно, доходит ли телеметрия."""
    with conn:
        conn.execute(
            "INSERT INTO otel_ingest (signal, last_at, batches, stored, dropped)"
            " VALUES (?, ?, 1, ?, ?)"
            " ON CONFLICT(signal) DO UPDATE SET"
            "   last_at = excluded.last_at,"
            "   batches = batches + 1,"
            "   stored  = stored + excluded.stored,"
            "   dropped = dropped + excluded.dropped",
            (signal, stamp(datetime.now(UTC)), stored, dropped),
        )


def prune(conn: sqlite3.Connection, keep_days: int, now: datetime | None = None) -> dict[str, int]:
    """Убрать телеметрию старше `keep_days` суток; 0 — не убирать ничего.

    Событий приходит по несколько на каждый ход и каждый вызов инструмента,
    около 400 байт на штуку: за месяц активной работы это сотни мегабайт, и
    без срока хранения БД растёт быстрее полезных данных. Данные парсера при
    этом не трогаются — у них своя история и свой смысл.
    """
    if keep_days <= 0:
        return {"metrics": 0, "events": 0}
    edge = stamp((now or datetime.now(UTC)) - timedelta(days=keep_days))
    removed = {}
    for table in ("otel_metrics", "otel_events"):
        before = conn.total_changes
        with conn:
            conn.execute(f"DELETE FROM {table} WHERE ts < ?", (edge,))  # noqa: S608
        removed[table.removeprefix("otel_")] = conn.total_changes - before
    if any(removed.values()):
        log.info("телеметрия старше %s суток убрана: %s", keep_days, removed)
    return removed


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Что приёмник видел: приёмы по сигналам и накопленное по именам."""
    signals = {
        row["signal"]: {
            "last_at": row["last_at"],
            "batches": row["batches"],
            "stored": row["stored"],
            "dropped": row["dropped"],
        }
        for row in conn.execute("SELECT * FROM otel_ingest ORDER BY signal")
    }
    metrics = [
        dict(row)
        for row in conn.execute(
            "SELECT name, COUNT(*) AS points, SUM(value) AS total, MAX(ts) AS last_at"
            " FROM otel_metrics GROUP BY name ORDER BY name"
        )
    ]
    events = [
        dict(row)
        for row in conn.execute(
            "SELECT name, COUNT(*) AS records, MAX(ts) AS last_at"
            " FROM otel_events GROUP BY name ORDER BY records DESC"
        )
    ]
    # Сколько всего накоплено и с какого дня: по этому видно, работает ли срок
    # хранения и во что данные обходятся на диске.
    stored = conn.execute(
        "SELECT SUM(rows) AS rows, SUM(bytes) AS bytes, MIN(oldest) AS oldest FROM ("
        "  SELECT COUNT(*) AS rows, COALESCE(SUM(LENGTH(attrs)), 0) AS bytes,"
        "         MIN(ts) AS oldest FROM otel_metrics"
        "  UNION ALL"
        "  SELECT COUNT(*), COALESCE(SUM(LENGTH(attrs)), 0), MIN(ts) FROM otel_events)"
    ).fetchone()
    return {
        "signals": signals,
        "metrics": metrics,
        "events": events,
        "stored": dict(stored),
    }


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TS_FORMAT)


def _metric_points(metric: Any, base: dict[str, Any]) -> tuple[list[MetricPoint], int]:
    """Точки одной метрики. Гистограммы Claude Code не шлёт — они в потери."""
    if not isinstance(metric, dict):
        return [], 1
    name = metric.get("name")
    if not isinstance(name, str) or not name:
        return [], 1
    body = _first(metric, "sum", "gauge")
    if not isinstance(body, dict):
        return [], 1
    points: list[MetricPoint] = []
    dropped = 0
    for raw in _items(body, "dataPoints", "data_points"):
        point = _point(name, raw, base)
        if point is None:
            dropped += 1
        else:
            points.append(point)
    return points, dropped


def _point(name: str, raw: Any, base: dict[str, Any]) -> MetricPoint | None:
    if not isinstance(raw, dict):
        return None
    ts = _stamp(_first(raw, "timeUnixNano", "time_unix_nano"))
    value = _number(raw)
    if ts is None or value is None:
        return None
    attrs = base | _attributes(raw.get("attributes"))
    return MetricPoint(
        name=name,
        ts=ts,
        start_ts=_stamp(_first(raw, "startTimeUnixNano", "start_time_unix_nano")),
        session_id=_text(attrs.get("session.id")),
        model=_text(attrs.get("model")),
        kind=_text(attrs.get("type")),
        value=value,
        attrs=attrs,
    )


def _event(record: Any, base: dict[str, Any]) -> EventRecord | None:
    if not isinstance(record, dict):
        return None
    attrs = base | _attributes(record.get("attributes"))
    name = _event_name(record, attrs)
    if name is None:
        return None
    # Время самого события может не приехать (`timeUnixNano` = 0) — тогда
    # остаётся момент приёма записи экспортёром.
    ts = _stamp(_first(record, "timeUnixNano", "time_unix_nano")) or _stamp(
        _first(record, "observedTimeUnixNano", "observed_time_unix_nano")
    )
    if ts is None:
        return None
    return EventRecord(name=name, ts=ts, session_id=_text(attrs.get("session.id")), attrs=attrs)


def _event_name(record: dict[str, Any], attrs: dict[str, Any]) -> str | None:
    """Имя события: поле записи, атрибут `event.name` или строковое тело.

    Где именно оно лежит, зависит от версии OTel SDK внутри Claude Code, а
    префикс `claude_code.` для нас лишний: в таблице все события его.
    """
    for candidate in (
        _first(record, "eventName", "event_name"),
        attrs.get("event.name"),
        _any_value(record.get("body")),
    ):
        if isinstance(candidate, str) and candidate:
            return candidate.removeprefix(PREFIX)
    return None


def _resource_attrs(resource: Any) -> dict[str, Any]:
    if not isinstance(resource, dict):
        return {}
    body = resource.get("resource")
    if not isinstance(body, dict):
        return {}
    return _attributes(body.get("attributes"))


def _attributes(items: Any, *, keep_all: bool = False) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if not isinstance(items, list):
        return attrs
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            continue
        key = item["key"]
        if keep_all or key not in SKIPPED_ATTRS:
            attrs[key] = _any_value(item.get("value"))
    return attrs


def _any_value(value: Any) -> Any:
    """Развернуть OTLP `AnyValue` в обычное значение Python."""
    if not isinstance(value, dict):
        return value
    for field in ("stringValue", "string_value", "boolValue", "bool_value", "bytesValue"):
        if field in value:
            return value[field]
    for field in ("intValue", "int_value"):
        if field in value:  # int64 в JSON едет строкой
            return _int(value[field])
    for field in ("doubleValue", "double_value"):
        if field in value:
            return _float(value[field])
    for field in ("arrayValue", "array_value"):
        if field in value:
            inner = value[field]
            values = inner.get("values") if isinstance(inner, dict) else None
            return [_any_value(item) for item in values] if isinstance(values, list) else []
    for field in ("kvlistValue", "kvlist_value"):
        if field in value:
            inner = value[field]
            values = inner.get("values") if isinstance(inner, dict) else None
            return _attributes(values, keep_all=True)
    return None


def _number(point: dict[str, Any]) -> float | None:
    """Значение точки: целое едет строкой, дробное числом."""
    for field in ("asInt", "as_int"):
        if field in point:
            number = _int(point[field])
            return None if number is None else float(number)
    for field in ("asDouble", "as_double"):
        if field in point:
            return _float(point[field])
    return None


def _stamp(nanos: Any) -> str | None:
    value = _int(nanos)
    if value is None or value <= 0:
        return None
    return stamp(datetime.fromtimestamp(value / 1e9, UTC))


def _items(source: Any, *names: str) -> list[Any]:
    value = _first(source, *names) if isinstance(source, dict) else None
    return value if isinstance(value, list) else []


def _first(source: Any, *names: str) -> Any:
    """Первое присутствующее поле: OTLP/JSON допускает и camelCase, и snake_case."""
    if not isinstance(source, dict):
        return None
    for name in names:
        if name in source:
            return source[name]
    return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _canonical(attrs: dict[str, Any]) -> str:
    return orjson.dumps(attrs, option=orjson.OPT_SORT_KEYS).decode("utf-8")


def _fingerprint(*parts: Any) -> str:
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()


def _insert(conn: sqlite3.Connection, sql: str, rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    before = conn.total_changes
    with conn:
        conn.executemany(sql, rows)
    return conn.total_changes - before
