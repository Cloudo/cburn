"""Доступ к SQLite. Схема — в schema.sql, применяется идемпотентно."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import paths

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: Path | None = None, *, apply_schema: bool = True) -> sqlite3.Connection:
    """Открыть соединение с применённой схемой.

    Путь берётся из `paths` в момент вызова, а не при импорте: так его можно
    подменить (тесты, вторая БД) без переимпорта модуля. `apply_schema=False` —
    для читающих соединений (каждый запрос API), чтобы не гонять schema.sql.
    """
    paths.ensure_dirs()
    conn = sqlite3.connect(db_path or paths.DB_PATH)
    conn.row_factory = sqlite3.Row
    if apply_schema:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn
