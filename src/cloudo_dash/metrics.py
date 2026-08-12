"""Запросы к БД. Тяжёлые агрегаты считаются в SQL, не в Python.

Пока здесь только то, что нужно для сверки цифр по сессии (задача A3);
метрики ТЗ §4 — burn rate по окнам, доля моделей, холостые ходы — задача B3.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


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
