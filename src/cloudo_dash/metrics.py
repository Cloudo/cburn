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
            SELECT s.id, p.slug AS project, s.last_at, s.turns, s.tokens_out,
                   s.cache_read, s.cache_write, s.last_context, s.first_prompt
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             ORDER BY s.last_at DESC LIMIT ?
            """,
            (limit,),
        )
    )


# --- обзор (задача A5) -------------------------------------------------------

#: Окна, в которых считается burn rate, минуты (ТЗ §4).
BURN_WINDOWS = (1, 5, 60)

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


def burn_rates(conn: sqlite3.Connection, now: datetime) -> dict[str, dict]:
    """Burn rate по окнам ТЗ §4 — в токенах в минуту."""
    rates: dict[str, dict] = {}
    for minutes in BURN_WINDOWS:
        usage = window_usage(conn, now - timedelta(minutes=minutes))
        rates[f"{minutes}m"] = {
            "tokens_per_min": usage["tokens"] / minutes,
            "output_per_min": usage["output_tokens"] / minutes,
            "cost_per_hour": usage["cost_usd"] * 60 / minutes,  # цены — задача B1
            "turns": usage["turns"],
            "sessions": usage["sessions"],
        }
    return rates


def live_sessions(conn: sqlite3.Connection, now: datetime, seconds: int = 120) -> list[dict]:
    """Сессии, в которых был ход за последние `seconds` (уточнение — задача B4)."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, p.slug AS project, s.last_at, s.turns, s.tokens_out,
                   s.last_context, s.first_prompt,
                   (SELECT COALESCE(SUM(t.output_tokens), 0) FROM turns AS t
                     WHERE t.session_id = s.id AND t.ts >= ?) AS output_recent
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE s.last_at >= ?
             ORDER BY s.last_at DESC
            """,
            (_utc_stamp(now - timedelta(seconds=seconds)),) * 2,
        )
    ]


def top_sessions(conn: sqlite3.Connection, since: datetime, limit: int = 5) -> list[dict]:
    """Самые расходные сессии периода."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, p.slug AS project, s.last_at, s.first_prompt, s.last_context,
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
        "totals": dict(totals),
    }
