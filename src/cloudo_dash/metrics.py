"""Запросы к БД. Тяжёлые агрегаты считаются в SQL, не в Python.

Пока здесь только то, что нужно для сверки цифр по сессии (задача A3);
метрики ТЗ §4 — burn rate по окнам, доля моделей, холостые ходы — задача B3.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class SessionSummary:
    """Итоги по одной сессии."""

    session_id: str
    project: str | None
    root_path: str | None
    title: str | None
    started_at: str | None
    last_at: str | None
    first_prompt: str | None
    turns: int
    sidechain_turns: int
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int
    cost_usd: float
    sidechain_tokens: int
    sidechain_cost_usd: float
    parent_session_id: str | None
    last_context: int

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h


def session_summary(conn: sqlite3.Connection, session_id: str) -> SessionSummary | None:
    """Суммы по сессии. Считаются из `turns`, а не из кэша в `sessions`."""
    row = conn.execute(
        """
        SELECT s.id                                        AS session_id,
               COALESCE(p.display_name, p.slug)            AS project,
               p.root_path                                 AS root_path,
               s.title                                     AS title,
               s.started_at, s.last_at, s.first_prompt,
               COUNT(t.id)                                 AS turns,
               COALESCE(SUM(t.is_sidechain), 0)            AS sidechain_turns,
               COALESCE(SUM(t.input_tokens), 0)            AS input_tokens,
               COALESCE(SUM(t.output_tokens), 0)           AS output_tokens,
               COALESCE(SUM(t.cache_read), 0)              AS cache_read,
               COALESCE(SUM(t.cache_write_5m), 0)          AS cache_write_5m,
               COALESCE(SUM(t.cache_write_1h), 0)          AS cache_write_1h,
               COALESCE(SUM(t.cost_usd), 0)                AS cost_usd,
               -- Расход сабагентов входит в сессию и виден отдельной строкой (ТЗ §4).
               COALESCE(SUM(t.is_sidechain * (t.input_tokens + t.output_tokens + t.cache_read
                          + t.cache_write_5m + t.cache_write_1h)), 0) AS sidechain_tokens,
               COALESCE(SUM(t.is_sidechain * t.cost_usd), 0) AS sidechain_cost_usd,
               s.parent_session_id,
               s.last_context
          FROM sessions AS s
          LEFT JOIN projects AS p ON p.id = s.project_id
          LEFT JOIN turns    AS t ON t.session_id = s.id
         WHERE s.id = ?
         GROUP BY s.id
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return SessionSummary(**dict(row))


def session_models(conn: sqlite3.Connection, session_id: str) -> list[tuple[str, int, int]]:
    """Распределение ходов и выходных токенов по моделям."""
    return [
        (row["model"] or "—", row["turns"], row["output_tokens"])
        for row in conn.execute(
            """
            SELECT model, COUNT(*) AS turns, SUM(output_tokens) AS output_tokens
              FROM turns WHERE session_id = ?
             GROUP BY model ORDER BY output_tokens DESC
            """,
            (session_id,),
        )
    ]


def session_tools(
    conn: sqlite3.Connection, session_id: str, limit: int = 10
) -> list[tuple[str, int]]:
    """Профиль инструментов сессии: для Bash детализация — задача B3."""
    return [
        (row["tool"], row["calls"])
        for row in conn.execute(
            """
            SELECT c.tool, COUNT(*) AS calls
              FROM tool_calls AS c JOIN turns AS t ON t.id = c.turn_id
             WHERE t.session_id = ?
             GROUP BY c.tool ORDER BY calls DESC LIMIT ?
            """,
            (session_id, limit),
        )
    ]


def session_tool_times(conn: sqlite3.Connection, session_id: str, limit: int = 10) -> list[dict]:
    """Сколько времени сессия провела в каждом инструменте (веха E).

    Длительность знает только телеметрия: в транскрипте между запросом
    инструмента и его результатом нет ничего, кроме двух отметок времени, а они
    включают и ожидание разрешения. `duration_ms` приходит строкой, отсюда CAST.
    """
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT json_extract(attrs, '$.tool_name')                    AS tool,
                   COUNT(*)                                              AS calls,
                   SUM(CAST(json_extract(attrs, '$.duration_ms') AS REAL)) / 1000.0 AS seconds,
                   MAX(CAST(json_extract(attrs, '$.duration_ms') AS REAL)) / 1000.0 AS slowest,
                   -- Атрибут `success` приходит строкой, а у части событий его
                   -- нет вовсе: без COALESCE сумма схлопнулась бы в NULL.
                   SUM(COALESCE(json_extract(attrs, '$.success') IN ('false', 0), 0)) AS failures
              FROM otel_events
             WHERE name = 'tool_result' AND session_id = ?
             GROUP BY tool ORDER BY seconds DESC LIMIT ?
            """,
            (session_id, limit),
        )
    ]


def recent_sessions(
    conn: sqlite3.Connection,
    limit: int = 20,
    project: str | None = None,
    since: datetime | None = None,
) -> list[sqlite3.Row]:
    """Последние сессии по активности, с фильтрами по проекту и периоду (B7)."""
    clause = ""
    params: list[Any] = []
    if project:
        clause += " AND p.slug LIKE ?"
        params.append(f"%{project}%")
    if since is not None:
        clause += " AND s.last_at >= ?"
        params.append(_utc_stamp(since))
    return list(
        conn.execute(
            f"""
            SELECT s.id, COALESCE(p.display_name, p.slug) AS project, s.last_at, s.started_at,
                   s.turns, s.tokens_out,
                   s.cache_read, s.cache_write, s.cost_usd, s.last_context, s.first_prompt,
                   s.title, s.title_source
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE s.hidden = 0{clause}
             ORDER BY s.last_at DESC LIMIT ?
            """,  # noqa: S608
            (*params, limit),
        )
    )


def period_start(period: str | None) -> datetime | None:
    """Начало периода: `today`, `24h`, `7d`, `all`, дата. None — вся история.

    Живёт здесь, а не в CLI: тем же разбором пользуется экран «Сессии».
    """
    value = (period or "all").strip().lower()
    now = datetime.now(UTC)
    if value in {"all", ""}:
        return None
    if value == "today":
        return local_day_start(now)
    if value.endswith(("h", "d")) and value[:-1].isdigit():
        hours = int(value[:-1]) * (24 if value.endswith("d") else 1)
        return now - timedelta(hours=hours)
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def known_projects(conn: sqlite3.Connection) -> list[dict]:
    """Проекты со счётчиком сессий — для выпадашки фильтра."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT p.slug, COALESCE(p.display_name, p.slug) AS name, p.root_path,
                   COUNT(s.id) AS sessions
              FROM projects AS p
              LEFT JOIN sessions AS s ON s.project_id = p.id AND s.hidden = 0
             GROUP BY p.id
             HAVING sessions > 0
             ORDER BY sessions DESC
            """
        )
    ]


def session_turns(conn: sqlite3.Connection, session_id: str, limit: int = 500) -> list[dict]:
    """Ходы сессии по порядку — для графика контекста и ленты (задача C2).

    Холостой ход считается тем же порогом, что и в сводке (ТЗ §6): короткий
    ответ при большом контексте. Флаг вычисляется в запросе, а не хранится,
    чтобы правка порога не требовала переиндексации.
    """
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.message_id, t.ts, t.model, t.output_tokens, t.input_tokens,
                   t.cache_read, t.cache_write_5m + t.cache_write_1h AS cache_write,
                   t.context_estimate, t.cost_usd, t.is_sidechain,
                   (t.output_tokens < :max_output
                    AND t.context_estimate > :min_context) AS is_idle,
                   (SELECT GROUP_CONCAT(c.tool, ' ') FROM tool_calls AS c
                     WHERE c.turn_id = t.id) AS tools
              FROM turns AS t
             WHERE t.session_id = :session
             ORDER BY t.ts, t.id
             LIMIT :limit
            """,
            {
                "session": session_id,
                "limit": limit,
                "max_output": IDLE_MAX_OUTPUT,
                "min_context": IDLE_MIN_CONTEXT,
            },
        )
    ]


def session_events(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Заметные моменты сессии: автосуммаризации и точки ветвления resume.

    Форк — это не запись в транскрипте, а связь между сессиями, поэтому он
    собирается из `parent_session_id`, а не из `session_events`.
    """
    marks = [
        dict(row)
        for row in conn.execute(
            "SELECT ts, kind FROM session_events WHERE session_id = ? ORDER BY ts",
            (session_id,),
        )
    ]
    marks += [
        {"ts": row["started_at"], "kind": "fork", "session_id": row["id"]}
        for row in conn.execute(
            "SELECT id, started_at FROM sessions WHERE parent_session_id = ? ORDER BY started_at",
            (session_id,),
        )
        if row["started_at"]
    ]
    return sorted(marks, key=lambda mark: mark["ts"] or "")


#: Статусы совета: новый, принят к работе, отклонён (задача D6).
ADVICE_STATUSES = ("new", "accepted", "rejected")


def advice_history(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """История разборов со вложенными советами (экран «Советы», задача D6)."""
    runs = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, ts, kind, period_start, period_end, model, cost_usd, max_severity
              FROM advice ORDER BY ts DESC LIMIT ?
            """,
            (limit,),
        )
    ]
    if not runs:
        return []
    ids = tuple(run["id"] for run in runs)
    items: dict[int, list[dict]] = {run["id"]: [] for run in runs}
    for row in conn.execute(
        f"""
        SELECT id, advice_id, key, title, severity, detail, action, evidence, status
          FROM advice_items WHERE advice_id IN ({",".join("?" * len(ids))})
         ORDER BY id
        """,  # noqa: S608
        ids,
    ):
        items[row["advice_id"]].append(dict(row))
    for run in runs:
        run["items"] = items[run["id"]]
        _attach_mentioned_sessions(conn, run["items"])
    return runs


#: Как советчик ссылается на сессию: коротким идентификатором из дайджеста.
#: Полный uuid он не видит — в дайджест уходит тот же короткий вид.
_SESSION_MENTION = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\b[0-9a-f]{8}\b")


def _attach_mentioned_sessions(conn: sqlite3.Connection, items: list[dict]) -> None:
    """Развернуть упомянутые в совете идентификаторы в имя сессии и проект.

    Название сессии в дайджест не уходит — это пересказ переписки (ТЗ §7).
    Но на экране оно нужно: «b2ae5a8a» человеку ничего не говорит. Поэтому
    разворачиваем здесь, при показе, и название с машины никуда не уезжает.
    """
    prefixes = {
        mention[:8]
        for item in items
        for field in ("title", "detail", "action", "evidence")
        for mention in _SESSION_MENTION.findall(item.get(field) or "")
    }
    if not prefixes:
        for item in items:
            item["sessions"] = []
        return
    known = {
        row["short"]: dict(row)
        for row in conn.execute(
            f"""
            SELECT substr(s.id, 1, 8) AS short, s.id, s.title,
                   COALESCE(p.display_name, p.slug) AS project
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE substr(s.id, 1, 8) IN ({",".join("?" * len(prefixes))})
            """,  # noqa: S608
            tuple(prefixes),
        )
    }
    for item in items:
        seen: dict[str, dict] = {}
        for field in ("title", "detail", "action", "evidence"):
            for mention in _SESSION_MENTION.findall(item.get(field) or ""):
                session = known.get(mention[:8])
                if session is not None:
                    seen[session["id"]] = session
        item["sessions"] = list(seen.values())


def set_advice_status(conn: sqlite3.Connection, item_id: int, status: str) -> bool:
    """Отметить совет принятым, отклонённым или вернуть в новые.

    Отклонённый уезжает в промпт следующего такта пометкой «не повторять» —
    этим и ценен статус (ТЗ §5).
    """
    if status not in ADVICE_STATUSES:
        raise ValueError(f"неизвестный статус совета: {status}")
    with conn:
        cursor = conn.execute("UPDATE advice_items SET status = ? WHERE id = ?", (status, item_id))
    return cursor.rowcount > 0


#: Сколько точек в спарклайне расхода сессии: столбик шире пары пикселей на
#: экране всё равно не разглядеть, а данных на каждую точку нужно тем меньше,
#: чем их больше.
SPARK_POINTS = 24


#: Что телеметрия знает о сессии: когда по ней было последнее событие вообще и
#: когда — последнее решение по разрешению. Оба списка сессий берут эти колонки,
#: чтобы статус считался одним правилом (веха E).
OTEL_SESSION_COLUMNS = """
                   (SELECT MAX(e.ts) FROM otel_events AS e
                     WHERE e.session_id = s.id)                        AS otel_seen_at,
                   (SELECT MAX(e.ts) FROM otel_events AS e
                     WHERE e.session_id = s.id
                       AND e.name = 'tool_decision')                   AS tool_decided_at,
"""


def sessions_page(
    conn: sqlite3.Connection,
    *,
    project: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> list[dict]:
    """Экран «Сессии»: список с фильтрами, спарклайном и связью resume (задача C1).

    Статус считается тем же правилом, что и на «Обзоре», но фильтруется уже
    в Python: он выводится из нескольких полей, и переносить это в SQL значит
    задвоить правило.
    """
    moment = now or datetime.now(UTC)
    clause = ""
    params: list[Any] = []
    if project:
        clause += " AND p.slug LIKE ?"
        params.append(f"%{project}%")
    if since is not None:
        clause += " AND s.last_at >= ?"
        params.append(_utc_stamp(since))
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT s.id, COALESCE(p.display_name, p.slug) AS project, p.root_path,
                   s.title, s.first_prompt, s.last_prompt, s.started_at, s.last_at,
                   s.turns, s.tokens_out, s.cache_read, s.cache_write, s.cost_usd,
                   s.last_context, s.parent_session_id, s.is_live, s.busy_since,
                   s.last_record_kind, s.last_record_at, s.last_stop_reason,
                   {OTEL_SESSION_COLUMNS}
                   (SELECT COUNT(*) FROM sessions AS child
                     WHERE child.parent_session_id = s.id)             AS children,
                   (SELECT COALESCE(SUM(t.is_sidechain), 0) FROM turns AS t
                     WHERE t.session_id = s.id)                        AS sidechain_turns
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE s.hidden = 0{clause}
             ORDER BY s.last_at DESC LIMIT ?
            """,  # noqa: S608
            (*params, limit),
        )
    ]
    for row in rows:
        row["status"] = session_status(row, moment)
        row["tokens"] = row["tokens_out"] + row["cache_read"] + row["cache_write"]
    if status:
        rows = [row for row in rows if row["status"] == status]
    _attach_sparklines(conn, rows)
    return rows


def _attach_sparklines(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Дорисовать каждой сессии её расход по времени — одним запросом на всех."""
    for row in rows:
        row["spark"] = []
    by_id = {row["id"]: row for row in rows}
    if not by_id:
        return
    ids = tuple(by_id)
    marks = conn.execute(
        f"""
        SELECT session_id,
               MIN(CAST(strftime('%s', ts) AS INTEGER)) AS started,
               MAX(CAST(strftime('%s', ts) AS INTEGER)) AS ended
          FROM turns WHERE session_id IN ({",".join("?" * len(ids))})
         GROUP BY session_id
        """,  # noqa: S608
        ids,
    ).fetchall()
    spans = {row["session_id"]: (row["started"], row["ended"]) for row in marks}
    buckets: dict[str, list[int]] = {sid: [0] * SPARK_POINTS for sid in spans}
    for row in conn.execute(
        f"""
        SELECT session_id, CAST(strftime('%s', ts) AS INTEGER) AS at,
               input_tokens + output_tokens + cache_read
             + cache_write_5m + cache_write_1h AS tokens
          FROM turns WHERE session_id IN ({",".join("?" * len(ids))})
        """,  # noqa: S608
        ids,
    ):
        started, ended = spans[row["session_id"]]
        span = max(ended - started, 1)
        index = min(int((row["at"] - started) / span * SPARK_POINTS), SPARK_POINTS - 1)
        buckets[row["session_id"]][index] += row["tokens"]
    for session_id, spark in buckets.items():
        by_id[session_id]["spark"] = spark


# --- обзор (задача A5) -------------------------------------------------------

#: Окна burn rate в секундах. Десятисекундное — «что происходит прямо сейчас»:
#: ход добавляет в окно сотни тысяч токенов разом, и в минутном усреднении это
#: видно как ступенька длиной в минуту (ТЗ §4).
BURN_WINDOWS = (10, 60, 300, 3600)

#: Формат времени в транскриптах: UTC с Z. Строковое сравнение здесь корректно
#: и позволяет фильтровать по времени прямо в SQL, без разбора дат.
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TS_FORMAT)


def project_filter(project: str | None, column: str = "session_id") -> tuple[str, list[str]]:
    """Условие «сессия из этого проекта» и его параметры (задача B7).

    Ищется подстрокой в slug — имени каталога транскриптов: оно содержит весь
    путь, поэтому `cloudo-dash` находит `-Users-cloudo-code-cloudo-dash`.
    """
    if not project:
        return "", []
    clause = (
        f" AND {column} IN (SELECT s.id FROM sessions AS s"
        " JOIN projects AS p ON p.id = s.project_id WHERE p.slug LIKE ?)"
    )
    return clause, [f"%{project}%"]


def window_usage(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    project: str | None = None,
) -> dict:
    """Суммы по ходам в интервале времени."""
    params: list[str] = [_utc_stamp(since)]
    clause = "ts >= ?"
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
    project_clause, project_params = project_filter(project)
    clause += project_clause
    params += project_params
    row = conn.execute(
        f"""
        SELECT COUNT(*)                            AS turns,
               COUNT(DISTINCT session_id)          AS sessions,
               COALESCE(SUM(input_tokens), 0)      AS input_tokens,
               COALESCE(SUM(output_tokens), 0)     AS output_tokens,
               COALESCE(SUM(cache_read), 0)        AS cache_read,
               COALESCE(SUM(cache_write_5m), 0)    AS cache_write_5m,
               COALESCE(SUM(cache_write_1h), 0)    AS cache_write_1h,
               COALESCE(SUM(cost_usd), 0)          AS cost_usd
          FROM turns WHERE {clause}
        """,  # noqa: S608 — параметры подставляются через плейсхолдеры
        params,
    ).fetchone()
    usage = dict(row)
    usage["cache_write"] = usage["cache_write_5m"] + usage["cache_write_1h"]
    # Полный объём токенов, прошедших через модель: им и живёт стрелка спидометра.
    usage["tokens"] = (
        usage["input_tokens"] + usage["output_tokens"] + usage["cache_read"] + usage["cache_write"]
    )
    return usage


def window_key(seconds: int) -> str:
    """Ключ окна в ответе API: 10s, 1m, 5m, 60m."""
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m"


def burn_rates(conn: sqlite3.Connection, now: datetime) -> dict[str, dict]:
    """Burn rate по окнам ТЗ §4 — всегда в токенах в минуту, окна разной длины."""
    rates: dict[str, dict] = {}
    for seconds in BURN_WINDOWS:
        usage = window_usage(conn, now - timedelta(seconds=seconds))
        minutes = seconds / 60
        rates[window_key(seconds)] = {
            "tokens_per_min": usage["tokens"] / minutes,
            "output_per_min": usage["output_tokens"] / minutes,
            "cost_per_hour": usage["cost_usd"] / minutes * 60,  # цены — задача B1
            "turns": usage["turns"],
            "sessions": usage["sessions"],
            "window_seconds": seconds,
            # Абсолютные суммы за окно: из них считается разбивка по
            # составляющим, и она должна уметь показывать любое окно.
            "usage": usage,
        }
    return rates


#: Сколько живых сессий показывать на дашборде.
LIVE_LIMIT = 5

#: Окно, в котором сессия ещё считается недавней и попадает на дашборд.
LIVE_WINDOW_SECONDS = 3600

#: После этой паузы сессия перестаёт быть «сейчас» и уходит в простой.
IDLE_AFTER_SECONDS = 120

#: Столько ждём ответа инструмента, прежде чем смотреть на процессы: до этого
#: любой инструмент считается работающим, разрешение так быстро не спрашивают.
PERMISSION_AFTER_SECONDS = 25

#: Насколько потомок может оказаться старше записи о запросе инструмента:
#: транскрипт дописывается порциями раз в 2-6 с и отстаёт от запуска процесса.
CHILD_LAG_SECONDS = 10

#: То же ожидание, когда работает телеметрия: решение по разрешению приходит
#: событием, и ждать четверть минуты незачем — событий хватает через секунды.
#: Порог всё же не нулевой: логи экспортируются пачкой раз в несколько секунд.
OTEL_PERMISSION_AFTER_SECONDS = 10

#: Насколько события могут отстать от хода, прежде чем телеметрия считается
#: замолчавшей (её могли выключить, перезапустив Claude Code без переменных).
OTEL_STALE_SECONDS = 60

#: Статусы сессии — по тому, кого она в этот момент ждёт.
STATUS_WORKING = "working"  # ход не завершён: модель думает или гоняет инструменты
STATUS_PERMISSION = "permission"  # инструмент запрошен, ответа нет — висит разрешение
STATUS_ANSWERED = "answered"  # модель ответила и ждёт человека
STATUS_IDLE = "idle"  # тишина дольше IDLE_AFTER_SECONDS, но процесс жив
STATUS_DONE = "done"  # процесса нет: в эту сессию больше не пишут


def session_status(row: dict, now: datetime) -> str:
    """Кого сессия ждёт прямо сейчас.

    Ход не завершён — работает модель: и когда она думает над промптом, и когда
    крутит инструменты. Завершённый ход означает обратное: ждут человека.
    Отдельный случай — инструмент запрошен, а результата нет: это либо долгий
    инструмент, либо висящий запрос разрешения, и в транскрипте они выглядят
    одинаково. Разводит их `_tool_is_running` — по процессам.

    Тишина сама по себе не отличает паузу от конца работы: транскрипт не знает,
    что сессия закрылась. Это знает `is_live` — флаг ставится по списку
    процессов Claude Code (задача B4). Отсутствие процесса засчитывается только
    после `IDLE_AFTER_SECONDS`: флаг обновляется не мгновенно, и живая сессия
    не должна мигать «закончилась» между опросами.

    Где включена телеметрия, догадка по процессам не нужна вовсе: решение по
    разрешению приходит событием `tool_decision` (веха E). Тогда «инструмент
    работает» — это факт, а не вывод из дерева процессов, и инструменты без
    своего процесса (MCP-вызовы, `WebFetch`) больше не выглядят ожиданием.
    """
    quiet = _seconds_since(row.get("last_record_at") or row.get("last_at"), now)
    kind = row.get("last_record_kind")

    if kind == "assistant" and row.get("last_stop_reason") == "tool_use":
        if _tool_is_allowed(row):
            return STATUS_WORKING
        if quiet < _permission_delay(row):
            return STATUS_WORKING
        if _otel_active(row):  # телеметрия молчит о решении — значит его и нет
            return STATUS_PERMISSION
        if _tool_is_running(row):
            return STATUS_WORKING
        return STATUS_PERMISSION
    if quiet >= IDLE_AFTER_SECONDS:
        return STATUS_DONE if row.get("is_live") == 0 else STATUS_IDLE
    if kind in {"prompt", "tool_result"}:
        return STATUS_WORKING
    return STATUS_ANSWERED


def _permission_delay(row: dict) -> float:
    """Сколько ждать ответа инструмента, прежде чем считать это разрешением."""
    return OTEL_PERMISSION_AFTER_SECONDS if _otel_active(row) else PERMISSION_AFTER_SECONDS


def _otel_active(row: dict) -> bool:
    """Шлёт ли эта сессия телеметрию прямо сейчас.

    Одних старых событий мало: телеметрию могли выключить посреди работы, и
    тогда молчание значило бы не «решения нет», а «данных нет». События идут
    на каждый ход, поэтому свежесть проверяется по последнему ходу.
    """
    seen = _moment(row.get("otel_seen_at"))
    if seen is None:
        return False
    asked = _moment(row.get("last_record_at") or row.get("last_at"))
    return asked is None or seen >= asked - timedelta(seconds=OTEL_STALE_SECONDS)


def _tool_is_allowed(row: dict) -> bool:
    """Разрешён ли уже инструмент, о котором просит модель (веха E).

    Событие `tool_decision` приходит и на автоматическое разрешение, и на
    ответ человека, поэтому решение позже запроса означает одно: сессия не
    ждёт, а работает. Отставание то же, что у процессов: транскрипт пишется
    порциями, а события уезжают пачкой раз в несколько секунд.
    """
    decided, asked = _moment(row.get("tool_decided_at")), _moment(row.get("last_record_at"))
    if decided is None or asked is None:
        return False
    return decided >= asked - timedelta(seconds=CHILD_LAG_SECONDS)


def _tool_is_running(row: dict) -> bool:
    """Гоняет ли сессия инструмент прямо сейчас — по процессам (задача B4).

    Признак работы — потомок процесса сессии, запущенный не раньше запроса
    инструмента. Постоянные потомки (MCP-серверы) и фоновые команды стартовали
    раньше и не в счёт, поэтому сравнение по времени, а не «есть потомки».
    Пока процессы не опрашивали (`is_live IS NULL`), признака нет — тогда
    остаётся прежняя догадка про висящее разрешение.
    """
    if row.get("is_live") is None:
        return False
    started, asked = _moment(row.get("busy_since")), _moment(row.get("last_record_at"))
    if started is None or asked is None:
        return False
    return started >= asked - timedelta(seconds=CHILD_LAG_SECONDS)


def _moment(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _seconds_since(stamp: str | None, now: datetime) -> float:
    moment = _moment(stamp)
    if moment is None:
        return float("inf")
    return (now - moment).total_seconds()


def live_sessions(
    conn: sqlite3.Connection,
    now: datetime,
    seconds: int = LIVE_WINDOW_SECONDS,
    limit: int = 40,
) -> list[dict]:
    """Недавние сессии с их статусами (уточнение живости — задача B4).

    Скрытые вручную не показываются, порядок — по последней активности.
    """
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT s.id, COALESCE(p.display_name, p.slug) AS project, p.root_path,
                   s.last_at, s.started_at,
                   s.turns, s.tokens_out, s.last_context, s.first_prompt, s.last_prompt,
                   s.title, s.title_source, s.last_record_kind, s.last_record_at,
                   s.last_stop_reason, s.is_live, s.busy_since,
                   {OTEL_SESSION_COLUMNS}
                   (SELECT COALESCE(SUM(t.output_tokens), 0) FROM turns AS t
                     WHERE t.session_id = s.id AND t.ts >= ?) AS output_recent
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE s.last_at >= ? AND s.hidden = 0
             ORDER BY s.last_at DESC
             LIMIT ?
            """,  # noqa: S608
            (_utc_stamp(now - timedelta(seconds=IDLE_AFTER_SECONDS)),)
            + (_utc_stamp(now - timedelta(seconds=seconds)), limit),
        )
    ]
    for row in rows:
        row["status"] = session_status(row, now)
    return rows


def session_chain(conn: sqlite3.Connection, session_id: str) -> dict:
    """Вся линия работы, к которой принадлежит сессия (задача B5).

    Resume копирует историю в новый `sessionId`, поэтому одна работа
    рассыпана по нескольким сессиям. Линия строится от корня цепочки вниз;
    ходы при этом не задваиваются — копии гасятся дедупликацией по
    `message_id` ещё при импорте.
    """
    rows = conn.execute(
        """
        WITH RECURSIVE up(id, parent) AS (
            SELECT id, parent_session_id FROM sessions WHERE id = :id
            UNION
            SELECT s.id, s.parent_session_id FROM sessions AS s JOIN up ON up.parent = s.id
        ),
        root AS (SELECT id FROM up WHERE parent IS NULL LIMIT 1),
        down(id) AS (
            SELECT id FROM root
            UNION
            SELECT s.id FROM sessions AS s JOIN down ON s.parent_session_id = down.id
        )
        SELECT d.id,
               (SELECT COUNT(*) FROM turns AS t WHERE t.session_id = d.id)          AS turns,
               (SELECT COALESCE(SUM(t.input_tokens + t.output_tokens + t.cache_read
                                  + t.cache_write_5m + t.cache_write_1h), 0)
                  FROM turns AS t WHERE t.session_id = d.id)                        AS tokens,
               (SELECT COALESCE(SUM(t.cost_usd), 0) FROM turns AS t
                 WHERE t.session_id = d.id)                                         AS cost_usd
          FROM down AS d
        """,
        {"id": session_id},
    ).fetchall()
    sessions = [dict(row) for row in rows]
    return {
        "sessions": [row["id"] for row in sessions],
        "turns": sum(row["turns"] for row in sessions),
        "tokens": sum(row["tokens"] for row in sessions),
        "cost_usd": sum(row["cost_usd"] for row in sessions),
    }


def refresh_liveness(conn: sqlite3.Connection, active: Mapping[str, datetime | None] | None) -> int:
    """Проставить `is_live` и `busy_since` по живым сессиям Claude Code (задача B4).

    `active=None` означает «спросить не удалось» — тогда флаги остаются как
    были: лучше устаревшая живость, чем ложное «закончилась» на всех. Значение
    сессии — момент запуска её самого молодого потомка (см. `processes`).
    Возвращает число изменённых строк.
    """
    if active is None:
        return 0
    ids = tuple(active)
    holes = ",".join("?" * len(ids))
    changed = 0
    with conn:
        # Умершие: живость гаснет вместе с занятостью — процесса нет.
        changed += conn.execute(
            f"UPDATE sessions SET is_live = 0, busy_since = NULL"  # noqa: S608
            f" WHERE (is_live IS NOT 0 OR busy_since IS NOT NULL)"
            f"{f' AND id NOT IN ({holes})' if ids else ''}",
            ids,
        ).rowcount
        for session_id, started in active.items():
            stamp = _utc_stamp(started) if started else None
            changed += conn.execute(
                "UPDATE sessions SET is_live = 1, busy_since = :stamp"
                " WHERE id = :id AND (is_live IS NOT 1 OR busy_since IS NOT :stamp)",
                {"id": session_id, "stamp": stamp},
            ).rowcount
    return changed


def top_sessions(conn: sqlite3.Connection, since: datetime, limit: int = 5) -> list[dict]:
    """Самые расходные сессии периода."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, COALESCE(p.display_name, p.slug) AS project, s.last_at,
                   s.first_prompt, s.title, s.last_context,
                   COUNT(t.id)                        AS turns,
                   COALESCE(SUM(t.output_tokens), 0)  AS output_tokens,
                   COALESCE(SUM(t.input_tokens + t.output_tokens
                                + t.cache_read + t.cache_write_5m
                                + t.cache_write_1h), 0) AS tokens
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
              JOIN turns AS t ON t.session_id = s.id AND t.ts >= ?
             GROUP BY s.id
             ORDER BY tokens DESC LIMIT ?
            """,
            (_utc_stamp(since), limit),
        )
    ]


def local_day_start(now: datetime) -> datetime:
    """Начало сегодняшнего дня по местному времени: «за сегодня» — про человека."""
    local = now.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


#: Чем телеметрия помечает служебные запросы Claude Code. Основная работа идёт
#: как `main`, сабагенты — как `subagent`, и те и другие видны в транскрипте.
#: `auxiliary` не виден там вовсе: это, например, генерация названия сессии.
OTEL_OFF_TRANSCRIPT = "auxiliary"

#: Источники решения по разрешению, означающие ответ человека, — остальные
#: (`config`, `hook`) отработали сами и внимания не требуют.
OTEL_MANUAL_SOURCES = ("user_permanent", "user_temporary", "user_abort", "user_reject")


def otel_usage(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    project: str | None = None,
) -> dict:
    """Расход, которого нет в транскриптах (веха E).

    Служебные запросы Claude Code — отдельные вызовы модели, и в JSONL от них
    не остаётся ничего: расход по ним дашборд занижает ровно на эту величину.
    Считается только по телеметрии; без неё все цифры нулевые.
    """
    clause = "ts >= ?"
    params: list[Any] = [_utc_stamp(since)]
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
    project_clause, project_params = project_filter(project)
    clause += project_clause
    params += project_params
    source = " AND json_extract(attrs, '$.query_source') = ?"

    tokens = {
        row["kind"]: row["value"]
        for row in conn.execute(
            f"SELECT kind, COALESCE(SUM(value), 0) AS value FROM otel_metrics"
            f" WHERE name = 'claude_code.token.usage' AND {clause}{source}"
            f" GROUP BY kind",  # noqa: S608
            (*params, OTEL_OFF_TRANSCRIPT),
        )
    }
    cost = conn.execute(
        f"SELECT COALESCE(SUM(value), 0) FROM otel_metrics"
        f" WHERE name = 'claude_code.cost.usage' AND {clause}{source}",  # noqa: S608
        (*params, OTEL_OFF_TRANSCRIPT),
    ).fetchone()[0]
    main_cost = conn.execute(
        f"SELECT COALESCE(SUM(value), 0) FROM otel_metrics"
        f" WHERE name = 'claude_code.cost.usage' AND {clause}"  # noqa: S608
        f" AND json_extract(attrs, '$.query_source') <> ?",
        (*params, OTEL_OFF_TRANSCRIPT),
    ).fetchone()[0]
    # Виды служебных запросов известны только событиям: в метриках все они
    # свалены в `auxiliary`, а `api_request` называет каждый по имени.
    kinds = [
        dict(row)
        for row in conn.execute(
            f"SELECT json_extract(attrs, '$.query_source') AS source, COUNT(*) AS requests,"
            f"       COALESCE(SUM(json_extract(attrs, '$.cost_usd')), 0) AS cost_usd"
            f"  FROM otel_events WHERE name = 'api_request' AND {clause}"  # noqa: S608
            f" GROUP BY source ORDER BY cost_usd DESC",
            params,
        )
    ]
    return {
        "tokens": sum(tokens.values()),
        "input_tokens": tokens.get("input", 0),
        "output_tokens": tokens.get("output", 0),
        "cache_read": tokens.get("cacheRead", 0),
        "cache_write": tokens.get("cacheCreation", 0),
        "cost_usd": cost,
        # Доля служебного в том, что телеметрия видела за тот же период.
        "share": cost / (cost + main_cost) if cost + main_cost else 0.0,
        "request_kinds": kinds,
    }


def otel_permissions(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    project: str | None = None,
) -> dict:
    """Решения по запросам разрешений (веха E).

    В транскрипт Claude Code не пишет ни запрос «разрешить?», ни ответ на него,
    поэтому до телеметрии этой цифры не было вовсе. Ручные подтверждения —
    прямой повод поправить `permissions` в настройках: каждое из них
    останавливает работу и ждёт человека.
    """
    clause = "ts >= ?"
    params: list[Any] = [_utc_stamp(since)]
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
    project_clause, project_params = project_filter(project)
    clause += project_clause
    params += project_params
    rows = [
        dict(row)
        for row in conn.execute(
            f"SELECT json_extract(attrs, '$.tool_name') AS tool,"
            f"       json_extract(attrs, '$.source')    AS source,"
            f"       json_extract(attrs, '$.decision')  AS decision,"
            f"       COUNT(*) AS decisions"
            f"  FROM otel_events WHERE name = 'tool_decision' AND {clause}"  # noqa: S608
            f" GROUP BY tool, source, decision",
            params,
        )
    ]
    manual: dict[str, int] = {}
    total = 0
    manual_total = 0
    rejected = 0
    for row in rows:
        total += row["decisions"]
        if row["decision"] == "reject":
            rejected += row["decisions"]
        if row["source"] in OTEL_MANUAL_SOURCES:
            manual_total += row["decisions"]
            tool = row["tool"] or "—"
            manual[tool] = manual.get(tool, 0) + row["decisions"]
    return {
        "decisions": total,
        "manual": manual_total,
        "auto": total - manual_total,
        "rejected": rejected,
        "by_tool": [
            {"tool": tool, "decisions": count}
            for tool, count in sorted(manual.items(), key=lambda item: -item[1])
        ],
    }


def otel_state(conn: sqlite3.Connection, since: datetime) -> dict:
    """Срез телеметрии для обзора: работает ли она и что видит (веха E)."""
    last_at = conn.execute(
        "SELECT MAX(last) FROM (SELECT MAX(ts) AS last FROM otel_metrics"
        " UNION ALL SELECT MAX(ts) FROM otel_events)"
    ).fetchone()[0]
    return {
        "active": last_at is not None,
        "last_at": last_at,
        "off_transcript": otel_usage(conn, since),
        "permissions": otel_permissions(conn, since),
    }


def overview(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """Сводка для главного экрана (ТЗ §5, «Обзор»)."""
    moment = now or datetime.now(UTC)
    day_start = local_day_start(moment)
    totals = conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM sessions) AS sessions,
               (SELECT COUNT(*) FROM turns)    AS turns,
               (SELECT COUNT(*) FROM projects) AS projects,
               (SELECT MAX(ts)  FROM turns)    AS last_turn_at
        """
    ).fetchone()
    return {
        "now": moment.astimezone(UTC).isoformat(),
        "burn": burn_rates(conn, moment),
        "today": window_usage(conn, day_start),
        "live_sessions": live_sessions(conn, moment),
        "live_limit": LIVE_LIMIT,
        "top_sessions": top_sessions(conn, day_start),
        "recent_turns": recent_turns(conn),
        "models": model_share(conn, day_start),
        "tools": tool_profile(conn, day_start),
        "idle": idle_turns(conn, day_start),
        "limits": limit_window(conn, moment),
        "series": burn_series(conn, moment),
        "stamps": data_stamps(conn, moment),
        "pending_sessions": pending_sessions(conn, moment),
        "otel": otel_state(conn, day_start),
        "series_bucket_seconds": SERIES_BUCKET_SECONDS,
        "totals": dict(totals),
    }


def data_stamps(conn: sqlite3.Connection, now: datetime) -> dict[str, str | None]:
    """Время самого свежего события в каждом срезе обзора.

    Виджет показывает не момент пересчёта (он идёт раз в секунду и без новых
    ходов), а время данных, на которых он стоит: в паузе метка честно замирает.
    Срезы разные, поэтому и времена разные: у ленты — последний ход вообще,
    у дневных виджетов — последний ход с местной полуночи.
    """
    row = conn.execute(
        """
        SELECT (SELECT MAX(ts) FROM turns)                       AS last_turn,
               (SELECT MAX(ts) FROM turns WHERE ts >= :day)      AS today_turn,
               (SELECT MAX(t.ts) FROM tool_calls AS c
                  JOIN turns AS t ON t.id = c.turn_id
                 WHERE t.ts >= :day)                             AS tool_call,
               (SELECT MAX(ts) FROM turns
                 WHERE ts >= :day AND output_tokens < :out
                   AND context_estimate > :ctx)                  AS idle_turn
        """,
        {
            "day": _utc_stamp(local_day_start(now)),
            "out": IDLE_MAX_OUTPUT,
            "ctx": IDLE_MIN_CONTEXT,
        },
    ).fetchone()
    return dict(row)


def recent_turns(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    """Лента последних ходов с инструментами, которые в них вызывались."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.message_id, t.session_id, t.ts, t.model, t.output_tokens,
                   t.input_tokens, t.cache_read, t.is_sidechain,
                   t.cache_write_5m + t.cache_write_1h AS cache_write,
                   t.context_estimate, COALESCE(p.display_name, p.slug) AS project,
                   (SELECT GROUP_CONCAT(c.tool, ' ') FROM tool_calls AS c
                     WHERE c.turn_id = t.id) AS tools
              FROM turns AS t
              LEFT JOIN sessions AS s ON s.id = t.session_id
              LEFT JOIN projects AS p ON p.id = s.project_id
             ORDER BY t.ts DESC, t.id DESC LIMIT ?
            """,
            (limit,),
        )
    ]


#: Шаг самописца. Мельче предела не сделать: Claude Code дописывает транскрипт
#: порциями раз в 2–6 секунд, а расход хода известен только по его завершении,
#: так что на двух секундах всплески уже разделяются пустыми корзинами.
SERIES_BUCKET_SECONDS = 2
SERIES_SPAN_MINUTES = 5


def burn_series(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    bucket_seconds: int = SERIES_BUCKET_SECONDS,
    span_minutes: int = SERIES_SPAN_MINUTES,
) -> list[dict]:
    """Расход по корзинам времени — лента самописца за последние минуты.

    Пустые корзины заполняются нулями: без них провал в работе выглядел бы
    как непрерывная нагрузка, только с редкими точками.
    """
    start = now - timedelta(minutes=span_minutes)
    edge = int(start.timestamp()) // bucket_seconds * bucket_seconds
    last = int(now.timestamp()) // bucket_seconds * bucket_seconds
    filled = {
        int(row["bucket"]): row
        for row in conn.execute(
            """
            SELECT CAST(strftime('%s', ts) AS INTEGER) / :step * :step AS bucket,
                   COUNT(*)                                            AS turns,
                   SUM(output_tokens)                                  AS output_tokens,
                   SUM(input_tokens + output_tokens + cache_read
                       + cache_write_5m + cache_write_1h)              AS tokens
              FROM turns
             WHERE ts >= :since
             GROUP BY bucket
            """,
            {"step": bucket_seconds, "since": _utc_stamp(start)},
        )
    }
    series: list[dict] = []
    for bucket in range(edge, last + bucket_seconds, bucket_seconds):
        row = filled.get(bucket)
        series.append(
            {
                "at": datetime.fromtimestamp(bucket, UTC).isoformat(),
                "turns": row["turns"] if row else 0,
                "tokens": row["tokens"] if row else 0,
                "output_tokens": row["output_tokens"] if row else 0,
            }
        )
    return series


def pending_sessions(conn: sqlite3.Connection, now: datetime, minutes: int = 10) -> list[str]:
    """Сессии, в которых модель работает прямо сейчас (ТЗ §4).

    Токены такого запроса ещё неизвестны: они появятся в транскрипте только
    вместе с завершённым ходом.
    """
    return [
        session["id"] for session in live_sessions(conn, now) if session["status"] == STATUS_WORKING
    ]


def set_hidden(conn: sqlite3.Connection, session_id: str, hidden: bool) -> bool:
    """Убрать сессию с дашборда или вернуть обратно. В транскриптах ничего не меняется."""
    with conn:
        cursor = conn.execute(
            "UPDATE sessions SET hidden = ? WHERE id = ?", (int(hidden), session_id)
        )
    return cursor.rowcount > 0


# --- метрики ТЗ §4 (задача B3) -----------------------------------------------

#: Холостой ход: модель ответила почти ничего, хотя контекст уже большой —
#: случай «жду» из отчёта (ТЗ §4). Пороги вынесены сюда, а не в конфиг:
#: это определение метрики, а не настройка.
IDLE_MAX_OUTPUT = 10
IDLE_MIN_CONTEXT = 50_000

#: Окно лимитов подписки: 5 часов с первого хода серии. Точку отсчёта Claude
#: Code в транскрипт не пишет, поэтому окно восстанавливается по данным и
#: помечается приближением (уточнение по OTel — веха E).
LIMIT_WINDOW_HOURS = 5
WEEK_HOURS = 24 * 7


def model_share(
    conn: sqlite3.Connection, since: datetime, project: str | None = None
) -> list[dict]:
    """Доля моделей за период: ходы и токены (ТЗ §4)."""
    clause, params = project_filter(project)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT COALESCE(model, '—')                      AS model,
                   COUNT(*)                                  AS turns,
                   COALESCE(SUM(output_tokens), 0)           AS output_tokens,
                   COALESCE(SUM(input_tokens + output_tokens + cache_read
                                + cache_write_5m + cache_write_1h), 0) AS tokens
              FROM turns WHERE ts >= ?{clause}
             GROUP BY model ORDER BY tokens DESC
            """,  # noqa: S608
            (_utc_stamp(since), *params),
        )
    ]


def tool_profile(
    conn: sqlite3.Connection, since: datetime, limit: int = 8, project: str | None = None
) -> dict:
    """Профиль инструментов за период; внутри Bash — по нормализованным командам."""
    clause, params = project_filter(project, "t.session_id")
    tools = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT c.tool, COUNT(*) AS calls
              FROM tool_calls AS c JOIN turns AS t ON t.id = c.turn_id
             WHERE t.ts >= ?{clause}
             GROUP BY c.tool ORDER BY calls DESC
            """,  # noqa: S608
            (_utc_stamp(since), *params),
        )
    ]
    commands = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT c.detail AS command, COUNT(*) AS calls
              FROM tool_calls AS c JOIN turns AS t ON t.id = c.turn_id
             WHERE t.ts >= ? AND c.tool = 'Bash' AND c.detail IS NOT NULL{clause}
             GROUP BY c.detail ORDER BY calls DESC LIMIT ?
            """,  # noqa: S608
            (_utc_stamp(since), *params, limit),
        )
    ]
    return {
        "tools": tools[:limit],
        "tools_total": sum(row["calls"] for row in tools),
        "bash_commands": commands,
    }


def idle_turns(conn: sqlite3.Connection, since: datetime, project: str | None = None) -> dict:
    """Холостые ходы за период: сколько их и во что обошлись."""
    clause, params = project_filter(project)
    row = conn.execute(
        f"""
        SELECT COUNT(*)                        AS turns,
               COALESCE(SUM(cache_read), 0)    AS cache_read,
               COALESCE(SUM(output_tokens), 0) AS output_tokens
          FROM turns
         WHERE ts >= ? AND output_tokens < ? AND context_estimate > ?{clause}
        """,  # noqa: S608
        (_utc_stamp(since), IDLE_MAX_OUTPUT, IDLE_MIN_CONTEXT, *params),
    ).fetchone()
    total = conn.execute(
        f"SELECT COUNT(*) FROM turns WHERE ts >= ?{clause}",  # noqa: S608
        (_utc_stamp(since), *params),
    ).fetchone()[0]
    idle = dict(row)
    idle["share"] = idle["turns"] / total if total else 0.0
    idle["max_output"] = IDLE_MAX_OUTPUT
    idle["min_context"] = IDLE_MIN_CONTEXT
    return idle


def limit_window(conn: sqlite3.Connection, now: datetime) -> dict:
    """Оценка окна лимитов подписки — приближение (ТЗ §4).

    Claude Code не пишет в транскрипт ни границ окна, ни самих лимитов, так
    что окно восстанавливается по ходам: оно начинается с первого хода после
    паузы длиннее пяти часов и столько же длится. Считаем расход внутри
    текущего окна и за скользящую неделю; «сколько осталось» без лимитов
    сказать нельзя, поэтому отдаём объём, а не проценты.
    """
    window_start = _current_window_start(conn, now)
    window = window_usage(conn, window_start) if window_start else None
    week = window_usage(conn, now - timedelta(hours=WEEK_HOURS))
    return {
        "approximate": True,
        "window_hours": LIMIT_WINDOW_HOURS,
        "started_at": window_start.isoformat() if window_start else None,
        "resets_at": (
            (window_start + timedelta(hours=LIMIT_WINDOW_HOURS)).isoformat()
            if window_start
            else None
        ),
        "usage": window,
        "week": week,
    }


def _current_window_start(conn: sqlite3.Connection, now: datetime) -> datetime | None:
    """Начало текущего пятичасового окна: первый ход после паузы длиннее окна."""
    rows = conn.execute(
        """
        SELECT ts FROM turns
         WHERE ts >= ? ORDER BY ts
        """,
        (_utc_stamp(now - timedelta(hours=LIMIT_WINDOW_HOURS * 4)),),
    ).fetchall()
    if not rows:
        return None
    span = timedelta(hours=LIMIT_WINDOW_HOURS)
    start = _parse_stamp(rows[0]["ts"])
    for row in rows[1:]:
        moment = _parse_stamp(row["ts"])
        if moment - start >= span:  # прошлое окно закрылось, началось новое
            start = moment
    return start


def _parse_stamp(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
