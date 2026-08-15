"""Tests of the advisor scheduler (task D3).

Every tick costs money, so the "call the model or not" decision is checked
especially closely: a switched-off advisor, a too early tick and a period without
turns must not lead to a call.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cburn.analyzer import scheduler
from cburn.db import connect

CONFIG = {
    "analyzer": {
        "enabled": True,
        "interval_minutes": 60,
        "model": "haiku",
        "weekly_deep_model": "sonnet",
    }
}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "scheduler.db")


def add_turn(conn: sqlite3.Connection, at: datetime, session: str = "s1") -> None:
    with conn:
        conn.execute("INSERT OR IGNORE INTO sessions (id) VALUES (?)", (session,))
        conn.execute(
            """
            INSERT INTO turns (message_id, session_id, ts, model, output_tokens)
            VALUES (?, ?, ?, 'claude-opus-5', 100)
            """,
            (f"msg-{at.timestamp()}-{session}", session, at.isoformat().replace("+00:00", "Z")),
        )


def add_tick(conn: sqlite3.Connection, kind: str, at: datetime) -> None:
    with conn:
        conn.execute(
            "INSERT INTO advice (ts, kind, digest_json, model, cost_usd)"
            " VALUES (?, ?, '{}', 'x', 0)",
            (at.isoformat(), kind),
        )


def test_disabled_analyzer_never_ticks(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC)
    add_turn(conn, now - timedelta(minutes=5))

    assert scheduler.plan_tick(conn, now, {"analyzer": {"enabled": False}}) is None


def test_quiet_period_is_skipped(conn: sqlite3.Connection) -> None:
    """There were no turns - nothing to advise about, no money spent."""
    now = datetime.now(UTC)
    add_tick(conn, scheduler.WEEKLY, now - timedelta(days=1))
    add_tick(conn, scheduler.HOURLY, now - timedelta(hours=2))

    assert scheduler.plan_tick(conn, now, CONFIG) is None


def test_hourly_tick_after_interval(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC)
    add_tick(conn, scheduler.WEEKLY, now - timedelta(days=1))
    add_tick(conn, scheduler.HOURLY, now - timedelta(hours=2))
    add_turn(conn, now - timedelta(minutes=30))

    tick = scheduler.plan_tick(conn, now, CONFIG)

    assert tick is not None
    assert (tick.kind, tick.model) == (scheduler.HOURLY, "haiku")


def test_tick_waits_for_the_interval(conn: sqlite3.Connection) -> None:
    """We do not tick early, even when turns keep coming."""
    now = datetime.now(UTC)
    add_tick(conn, scheduler.WEEKLY, now - timedelta(days=1))
    add_tick(conn, scheduler.HOURLY, now - timedelta(minutes=10))
    add_turn(conn, now - timedelta(minutes=5))

    assert scheduler.plan_tick(conn, now, CONFIG) is None


def test_weekly_tick_wins_and_takes_the_bigger_model(conn: sqlite3.Connection) -> None:
    """The weekly analysis runs on a bigger model and outranks the hourly one."""
    now = datetime.now(UTC)
    add_tick(conn, scheduler.HOURLY, now - timedelta(hours=2))
    add_turn(conn, now - timedelta(days=2))

    tick = scheduler.plan_tick(conn, now, CONFIG)

    assert tick is not None
    assert (tick.kind, tick.model) == (scheduler.WEEKLY, "sonnet")


def test_warmup_holds_the_first_tick(conn: sqlite3.Connection) -> None:
    """A server restart must not cost money by itself."""
    now = datetime.now(UTC)
    add_turn(conn, now - timedelta(minutes=5))

    assert scheduler.plan_tick(conn, now, CONFIG, started_at=now - timedelta(minutes=1)) is None
    assert scheduler.plan_tick(conn, now, CONFIG, started_at=now - timedelta(hours=1)) is not None


def test_run_tick_records_its_kind(conn: sqlite3.Connection) -> None:
    """The tick kind is written into history: the next weekly one is counted from it."""
    now = datetime.now(UTC)
    add_turn(conn, now - timedelta(minutes=10))
    envelope = {
        "is_error": False,
        "total_cost_usd": 0.03,
        "structured_output": {
            "advice": [
                {
                    "title": "Close the work line",
                    "severity": "warn",
                    "detail": "",
                    "action": "",
                    "evidence": "chains[0].sessions = 19",
                }
            ]
        },
        "modelUsage": {"claude-sonnet-5": {}},
    }

    result = scheduler.run_tick(
        conn,
        CONFIG,
        scheduler.Tick(scheduler.WEEKLY, now - timedelta(days=7), "sonnet"),
        runner=lambda prompt, model, budget, language="en": envelope,
    )

    assert result["kind"] == scheduler.WEEKLY
    row = conn.execute("SELECT kind, model, cost_usd FROM advice").fetchone()
    assert row["kind"] == scheduler.WEEKLY
    assert row["model"] == "claude-sonnet-5"
    # The next weekly tick is counted from this one, not from the calendar.
    assert scheduler.plan_tick(conn, now, CONFIG) is None
    digest_json = conn.execute("SELECT digest_json FROM advice").fetchone()["digest_json"]
    assert json.loads(digest_json)["usage"]["turns"] == 1
