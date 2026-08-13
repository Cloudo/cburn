"""Доступ к SQLite. Схема — в schema.sql, применяется идемпотентно."""

from __future__ import annotations

import sqlite3
from pathlib import Path, PurePosixPath

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
        _add_missing_columns(conn)
        _fill_project_names(conn)
    return conn


#: Колонки, добавленные в уже существующие таблицы. `CREATE TABLE IF NOT EXISTS`
#: старую таблицу не трогает, а пересоздавать базу из-за одной колонки незачем.
ADDED_COLUMNS = {"raw_events": {"version": "TEXT"}}


def _fill_project_names(conn: sqlite3.Connection) -> None:
    """Проставить имена проектам, проиндексированным до их появления.

    Имя считается из рабочего пути; переиндексация ради него не нужна.
    """
    rows = conn.execute(
        "SELECT id, root_path FROM projects WHERE display_name IS NULL AND root_path IS NOT NULL"
    ).fetchall()
    with conn:
        for row in rows:
            name = PurePosixPath(row["root_path"]).name
            if name:
                conn.execute("UPDATE projects SET display_name = ? WHERE id = ?", (name, row["id"]))


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        known = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, kind in columns.items():
            if column not in known:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")  # noqa: S608
