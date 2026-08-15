"""Receiving official Claude Code telemetry over OTLP (milestone E, TZ §2).

Claude Code can export metrics and events over OTLP; it is switched on by environment
variables (`cburn otel` prints which ones). The specification was checked against
https://code.claude.com/docs/en/monitoring-usage on 14 August 2026.

Of the three OTLP encodings we take `http/json`: it is parsed by the standard json
module without grpcio or protobuf, and the receiver lives right inside `cburn serve`
without occupying a second port. Bodies arrive at `/otlp/v1/metrics` and `/otlp/v1/logs`.

Telemetry is a second channel, not a replacement for the parser: it is in beta at
Anthropic, so the data lands in tables of its own (`otel_metrics`, `otel_events`), and
whether the two channels agree is what reconciliation E3 checks.

Parsing is as tolerant as the transcript parser's: unknown fields are ignored, an
incomprehensible piece of a payload counts as lost (`dropped`) and does not bring the
rest of the batch down - otherwise a format change would cut reception off entirely.
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

#: The time format is shared with transcript metrics: UTC with Z, compared as a string.
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

#: Claude Code name prefix: `claude_code.token.usage`, event `claude_code.api_request`.
PREFIX = "claude_code."

#: Attributes that are never stored in the database (TZ §7).
#:
#: The first group is identical for the machine: the email and account ids repeat in
#: every row and speak about the person, not about the spend.
#:
#: The second is the content of the work. By default Claude Code sends `<REDACTED>`
#: instead of it, but `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES`,
#: `OTEL_LOG_TOOL_DETAILS` and `OTEL_LOG_RAW_API_BODIES` switch the real texts on.
#: A human may switch them on for their own debugging - and that is no reason for the
#: dashboard to become a conversation store: lengths and counters are enough for us.
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
        "prompt",
        "response",
        "error",
        "tool_input",
        "tool_parameters",
        "body",
        "body_ref",
    }
)

#: OTLP signals the receiver accepts. Traces (the beta `ENHANCED_TELEMETRY`) are
#: acknowledged but not parsed: there is nothing to count in them, and a 404 would make
#: the exporter hammer away with retries.
SIGNALS = ("metrics", "logs", "traces")


@dataclass(frozen=True)
class MetricPoint:
    """A metric point: name, window, value and all attributes as they came."""

    name: str
    ts: str
    start_ts: str | None
    session_id: str | None
    model: str | None
    kind: str | None  # attribute type: input | output | cacheRead | cacheCreation | added | ...
    value: float
    attrs: dict[str, Any]

    @property
    def key(self) -> str:
        """The point fingerprint: within one window there is exactly one per attribute set."""
        return _fingerprint(self.name, self.start_ts, self.ts, _canonical(self.attrs))


@dataclass(frozen=True)
class EventRecord:
    """A telemetry event (OTLP log record) with a flat set of attributes."""

    name: str
    ts: str
    session_id: str | None
    attrs: dict[str, Any]

    @property
    def key(self) -> str:
        """The event fingerprint.

        `event.sequence` grows monotonically inside a session, so together with the session
        and the time it names the event unambiguously. Without the counter the fingerprint
        falls back to the attributes - which also swallows a repeat of the same payload.
        """
        sequence = self.attrs.get("event.sequence")
        if self.session_id and sequence is not None:
            return _fingerprint(self.session_id, sequence, self.name, self.ts)
        return _fingerprint(self.name, self.ts, _canonical(self.attrs))


def decode(body: bytes, content_encoding: str | None = None) -> Any:
    """Parse the OTLP/JSON request body, unpacking gzip when needed."""
    if content_encoding and "gzip" in content_encoding.lower():
        body = gzip.decompress(body)
    return orjson.loads(body)


def ingest(conn: sqlite3.Connection, signal: str, payload: Any) -> dict[str, int]:
    """Parse a payload and store it; return the counters for the answer and the logs."""
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
    """Parse `ExportMetricsServiceRequest`; return the points and the number of losses."""
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
    """Parse `ExportLogsServiceRequest`: Claude Code events travel as logs."""
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
    """Store the points, swallowing payload repeats; return the number of new rows."""
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
    """Mark a payload as received: these counters show whether telemetry arrives at all."""
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
    """Remove telemetry older than `keep_days` days; 0 removes nothing.

    Events arrive in bunches on every turn and every tool call, around 400 bytes each:
    over a month of active work that is hundreds of megabytes, and without a retention
    the database outgrows the useful data. Parser data is left alone in the process -
    it has its own history and its own meaning.
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
        log.info("telemetry older than %s days removed: %s", keep_days, removed)
    return removed


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    """What the receiver has seen: receptions per signal and totals per name."""
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
    # How much has piled up in total and since which day: this shows whether the
    # retention works and what the data costs on disk.
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
    """Points of one metric. Claude Code sends no histograms - those go to losses."""
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
    # The event's own time may not arrive (`timeUnixNano` = 0) - then the moment
    # the exporter recorded it is what is left.
    ts = _stamp(_first(record, "timeUnixNano", "time_unix_nano")) or _stamp(
        _first(record, "observedTimeUnixNano", "observed_time_unix_nano")
    )
    if ts is None:
        return None
    return EventRecord(name=name, ts=ts, session_id=_text(attrs.get("session.id")), attrs=attrs)


def _event_name(record: dict[str, Any], attrs: dict[str, Any]) -> str | None:
    """The event name: a record field, the `event.name` attribute or a string body.

    Where exactly it sits depends on the OTel SDK version inside Claude Code, and the
    `claude_code.` prefix is redundant for us: in this table every event carries it.
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


def _attributes(items: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if not isinstance(items, list):
        return attrs
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            continue
        key = item["key"]
        if key not in SKIPPED_ATTRS:
            attrs[key] = _any_value(item.get("value"))
    return attrs


def _any_value(value: Any) -> Any:
    """Unfold an OTLP `AnyValue` into a plain Python value."""
    if not isinstance(value, dict):
        return value
    for field in ("stringValue", "string_value", "boolValue", "bool_value", "bytesValue"):
        if field in value:
            return value[field]
    for field in ("intValue", "int_value"):
        if field in value:  # int64 travels as a string in JSON
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
            # A nested list of pairs is parsed by the same rule: the content of the
            # work must not seep into the database through a kvlist.
            return _attributes(values)
    return None


def _number(point: dict[str, Any]) -> float | None:
    """The point value: an integer travels as a string, a float as a number."""
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
    """The first field present: OTLP/JSON allows both camelCase and snake_case."""
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
