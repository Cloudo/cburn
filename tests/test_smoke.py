"""Smoke tests of the skeleton: the database schema applies, the config defaults are read."""

from cburn import config
from cburn.db import connect


def test_schema_applies(tmp_path):
    conn = connect(tmp_path / "cburn.db")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"files", "projects", "sessions", "turns", "tool_calls", "advice"} <= tables


def test_schema_grows_on_an_existing_database(tmp_path):
    """The human's database is already packed with history - new tables must appear
    in it by themselves, without touching what is already there (milestone E)."""
    path = tmp_path / "old.db"
    old = connect(path)
    with old:  # the state before milestone E: everything in place, no telemetry at all
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
    cfg = config.load(tmp_path / "no-such.toml")
    assert cfg["server"]["port"] == 8799
    assert cfg["analyzer"]["model"] == "haiku"


def test_config_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    cfg = config.load(path)
    cfg["thresholds"]["context_crit"] = 120_000
    config.save(cfg, path)
    assert config.load(path)["thresholds"]["context_crit"] == 120_000
