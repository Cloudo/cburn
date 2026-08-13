"""Запросы к БД. Тяжёлые агрегаты считаются в SQL, не в Python.

Пока здесь только то, что нужно для сверки цифр по сессии (задача A3);
метрики ТЗ §4 — burn rate по окнам, доля моделей, холостые ходы — задача B3.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


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
    last_context: int

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h


def session_summary(conn: sqlite3.Connection, session_id: str) -> SessionSummary | None:
    """Суммы по сессии. Считаются из `turns`, а не из кэша в `sessions`."""
    row = conn.execute(
        """
        SELECT s.id                                        AS session_id,
               p.slug                                      AS project,
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


def recent_sessions(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    """Последние сессии по активности (фильтры по проекту и периоду — задача B7)."""
    return list(
        conn.execute(
            """
            SELECT s.id, p.slug AS project, s.last_at, s.started_at, s.turns, s.tokens_out,
                   s.cache_read, s.cache_write, s.last_context, s.first_prompt,
                   s.title, s.title_source
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE s.hidden = 0
             ORDER BY s.last_at DESC LIMIT ?
            """,
            (limit,),
        )
    )


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


def window_usage(conn: sqlite3.Connection, since: datetime, until: datetime | None = None) -> dict:
    """Суммы по ходам в интервале времени."""
    params: list[str] = [_utc_stamp(since)]
    clause = "ts >= ?"
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
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


def live_sessions(
    conn: sqlite3.Connection, now: datetime, seconds: int = 120, limit: int = LIVE_LIMIT
) -> list[dict]:
    """Сессии, в которых был ход за последние `seconds` (уточнение — задача B4).

    Скрытые вручную не показываются, порядок — по последней активности.
    """
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, p.slug AS project, p.root_path, s.last_at, s.started_at,
                   s.turns, s.tokens_out, s.last_context, s.first_prompt,
                   s.title, s.title_source,
                   (SELECT COALESCE(SUM(t.output_tokens), 0) FROM turns AS t
                     WHERE t.session_id = s.id AND t.ts >= ?) AS output_recent
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE s.last_at >= ? AND s.hidden = 0
             ORDER BY s.last_at DESC
             LIMIT ?
            """,
            (_utc_stamp(now - timedelta(seconds=seconds)),) * 2 + (limit,),
        )
    ]


def top_sessions(conn: sqlite3.Connection, since: datetime, limit: int = 5) -> list[dict]:
    """Самые расходные сессии периода."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, p.slug AS project, s.last_at, s.first_prompt, s.title, s.last_context,
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
        "top_sessions": top_sessions(conn, day_start),
        "recent_turns": recent_turns(conn),
        "series": burn_series(conn, moment),
        "pending_sessions": pending_sessions(conn, moment),
        "series_bucket_seconds": SERIES_BUCKET_SECONDS,
        "totals": dict(totals),
    }


def recent_turns(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    """Лента последних ходов с инструментами, которые в них вызывались."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.message_id, t.session_id, t.ts, t.model, t.output_tokens,
                   t.input_tokens, t.cache_read, t.is_sidechain,
                   t.cache_write_5m + t.cache_write_1h AS cache_write,
                   t.context_estimate, p.slug AS project,
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


#: Шаг самописца. Мельче нет смысла: Claude Code дописывает транскрипт
#: порциями раз в 2–6 секунд, а расход хода известен только по его завершении.
SERIES_BUCKET_SECONDS = 5
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
    """Сессии, где запрос уже отправлен, а ответ ещё не дописан (ТЗ §4).

    Признак — последняя запись сессии не ход ассистента: промпт или результат
    инструмента лежит без ответа. Токены такого запроса ещё неизвестны: они
    появятся в транскрипте только вместе с завершённым ходом.
    """
    return [
        str(row["id"])
        for row in conn.execute(
            """
            SELECT id FROM sessions
             WHERE last_record_kind IN ('prompt', 'tool_result')
               AND last_record_at >= ?
            """,
            (_utc_stamp(now - timedelta(minutes=minutes)),),
        )
    ]


def set_hidden(conn: sqlite3.Connection, session_id: str, hidden: bool) -> bool:
    """Убрать сессию с дашборда или вернуть обратно. В транскриптах ничего не меняется."""
    with conn:
        cursor = conn.execute(
            "UPDATE sessions SET hidden = ? WHERE id = ?", (int(hidden), session_id)
        )
    return cursor.rowcount > 0


def session_cwd(conn: sqlite3.Connection, session_id: str) -> str | None:
    """Рабочий каталог сессии — по нему ищется её процесс."""
    row = conn.execute(
        """
        SELECT p.root_path FROM sessions AS s
          LEFT JOIN projects AS p ON p.id = s.project_id
         WHERE s.id = ?
        """,
        (session_id,),
    ).fetchone()
    return str(row["root_path"]) if row and row["root_path"] else None
