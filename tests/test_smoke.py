"""Дымовые тесты каркаса: схема БД применяется, дефолты конфига читаются."""

from cloudo_dash import config
from cloudo_dash.db import connect


def test_schema_applies(tmp_path):
    conn = connect(tmp_path / "cloudo-dash.db")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"files", "projects", "sessions", "turns", "tool_calls", "advice"} <= tables


def test_schema_grows_on_an_existing_database(tmp_path):
    """База у человека уже набита историей — новые таблицы должны появиться
    в ней сами, не тронув то, что там лежит (веха E)."""
    path = tmp_path / "старая.db"
    old = connect(path)
    with old:  # состояние до вехи E: всё на месте, телеметрии нет вовсе
        old.execute("INSERT INTO sessions (id) VALUES ('s1')")
        old.execute(
            "INSERT INTO turns (message_id, session_id, ts) VALUES ('m1', 's1', ?)",
            ("2026-08-14T07:00:00.000000Z",),
        )
        for table in ("otel_metrics", "otel_events", "otel_ingest"):
            old.execute(f"DROP TABLE {table}")  # noqa: S608
    old.close()

    conn = connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"otel_metrics", "otel_events", "otel_ingest"} <= tables
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    conn.close()


def test_config_defaults_without_file(tmp_path):
    cfg = config.load(tmp_path / "нет-такого.toml")
    assert cfg["server"]["port"] == 8799
    assert cfg["analyzer"]["model"] == "haiku"


def test_config_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    cfg = config.load(path)
    cfg["thresholds"]["context_crit"] = 120_000
    config.save(cfg, path)
    assert config.load(path)["thresholds"]["context_crit"] == 120_000
