"""SQLite access. The schema lives in schema.sql and is applied idempotently."""

from __future__ import annotations

import sqlite3
from pathlib import Path, PurePosixPath

from .. import paths

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: Path | None = None, *, apply_schema: bool = True) -> sqlite3.Connection:
    """Open a connection with the schema applied.

    The path is taken from `paths` at call time, not at import: that way it can be
    swapped (tests, a second database) without re-importing the module. `apply_schema=False`
    is for reading connections (every API request), to avoid running schema.sql.
    """
    paths.ensure_dirs()
    conn = sqlite3.connect(db_path or paths.DB_PATH)
    conn.row_factory = sqlite3.Row
    if apply_schema:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _add_missing_columns(conn)
        _fill_project_names(conn)
    return conn


#: Columns added to already existing tables. `CREATE TABLE IF NOT EXISTS`
#: leaves an old table alone, and recreating the database over one column is pointless.
ADDED_COLUMNS = {
    "raw_events": {"version": "TEXT"},
    "sessions": {"busy_since": "TEXT"},
    "advice": {"kind": "TEXT"},
}


def _fill_project_names(conn: sqlite3.Connection) -> None:
    """Fill in names for projects indexed before names existed.

    The name is derived from the working path; no reindex is needed for it.
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
