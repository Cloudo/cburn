"""Дымовые тесты каркаса: схема БД применяется, дефолты конфига читаются."""

from cloudo_dash import config
from cloudo_dash.db import connect


def test_schema_applies(tmp_path):
    conn = connect(tmp_path / "cloudo-dash.db")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"files", "projects", "sessions", "turns", "tool_calls", "advice"} <= tables


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
