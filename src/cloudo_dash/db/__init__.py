"""Доступ к SQLite. Схема — в schema.sql, применяется идемпотентно."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..paths import DB_PATH, ensure_dirs

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Открыть соединение с применённой схемой."""
    ensure_dirs()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn
