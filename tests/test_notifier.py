"""Notification tests (task D5, TZ §7).

No network is raised here: the channel is swapped, and the rules are checked as pure
functions - they were written that way exactly to make it possible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from cburn import notifier
from cburn.db import connect
from cburn.notifier import rules

NOW = datetime(2026, 8, 14, 18, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path: Path) -> Any:
    connection = connect(tmp_path / "notify.db")
    yield connection
    connection.close()


# --- rules ---------------------------------------------------------------------


def test_digest_goes_only_when_there_is_something_to_say() -> None:
    """An hourly summary without important tips is spam, not a benefit."""
    assert rules.digest_message("info", "text") is None
    assert rules.digest_message(None, "text") is None
    assert rules.digest_message("warn", "text").kind == "digest"
    assert rules.digest_message("crit", "text").severity == "crit"


def test_daily_summary_goes_once_a_day() -> None:
    """The digest goes out after the appointed hour and exactly once."""
    morning = datetime(2026, 8, 14, 9, 0).astimezone()
    evening = datetime(2026, 8, 14, 21, 30).astimezone()

    assert rules.daily_message(morning, "21:00", None, "total") is None
    first = rules.daily_message(evening, "21:00", None, "total")
    assert first is not None and first.kind == "daily"
    # Already sent today after the deadline - we do not send a second time.
    assert rules.daily_message(evening, "21:00", evening, "total") is None
    # Yesterday's send does not cancel today's.
    assert rules.daily_message(evening, "21:00", evening - timedelta(days=1), "total") is not None


def test_daily_summary_survives_broken_time() -> None:
    """A malformed time in the config must not bring the tick down."""
    assert rules.daily_message(NOW, "not a time", None, "total") is None


def test_alerts_respect_cooldown() -> None:
    """One session does not wake you more often than once every half hour."""
    candidates = [("s1", "warn", "context 160k")]
    assert rules.alert_messages(NOW, candidates, {}) != []
    assert rules.alert_messages(NOW, candidates, {"s1": NOW - timedelta(minutes=10)}) == []
    assert rules.alert_messages(NOW, candidates, {"s1": NOW - timedelta(minutes=31)}) != []


def test_pause_lets_crit_through() -> None:
    """The pause means "do not bother me with small things", not "switch the instrument off"."""
    quiet = NOW + timedelta(hours=1)
    assert rules.allowed(rules.Message("alert", "…", "warn"), quiet, NOW) is False
    assert rules.allowed(rules.Message("alert", "…", "crit"), quiet, NOW) is True
    assert rules.allowed(rules.Message("alert", "…", "warn"), None, NOW) is True


# --- sending and memory ---------------------------------------------------------


class Sent:
    """A channel that goes nowhere but remembers what was sent."""

    def __init__(self, mode: str = "bridge", error: str | None = None) -> None:
        self.mode = mode
        self.enabled = mode in {"bridge", "bot"}
        self.error = error
        self.messages: list[tuple[str, str]] = []

    def send(self, text: str, severity: str = "info", silent: bool | None = None) -> str | None:
        self.messages.append((severity, text))
        return self.error


def test_dispatch_records_what_was_sent(conn: Any) -> None:
    channel = Sent()
    with mock.patch.object(notifier, "Channel", return_value=channel):
        sent = notifier.dispatch(conn, [rules.Message("alert", "burning", "crit", "s1")], {})
    assert sent == 1
    assert channel.messages == [("crit", "burning")]
    row = conn.execute("SELECT kind, key, severity, ok FROM notifications").fetchone()
    assert (row["kind"], row["key"], row["severity"], row["ok"]) == ("alert", "s1", "crit", 1)


def test_failed_send_is_remembered_as_failed(conn: Any) -> None:
    """A failure shows in the history: otherwise the cooldown hides what never arrived."""
    channel = Sent(error="the bridge answered 500")
    with mock.patch.object(notifier, "Channel", return_value=channel):
        sent = notifier.dispatch(conn, [rules.Message("alert", "burning", "warn", "s1")], {})
    assert sent == 0
    assert conn.execute("SELECT ok FROM notifications").fetchone()[0] == 0


def test_pause_holds_everything_but_crit(conn: Any) -> None:
    notifier.set_pause(conn, NOW + timedelta(hours=1))
    channel = Sent()
    with mock.patch.object(notifier, "Channel", return_value=channel):
        notifier.dispatch(
            conn,
            [
                rules.Message("alert", "a trifle", "warn", "s1"),
                rules.Message("alert", "burning", "crit", "burn"),
            ],
            {},
            now=NOW,
        )
    assert [severity for severity, _ in channel.messages] == ["crit"]


def test_off_channel_sends_nothing(conn: Any) -> None:
    """The `off` channel disables sending, but the instrument keeps counting."""
    channel = Sent(mode="off")
    with mock.patch.object(notifier, "Channel", return_value=channel):
        assert notifier.dispatch(conn, [rules.Message("daily", "total")], {}) == 0
    assert channel.messages == []
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0


def test_alerts_come_from_the_same_numbers_as_the_dashboard(conn: Any) -> None:
    """The alert does no counting of its own: the thresholds and numbers are those on screen."""
    overview = {
        "burn": {"1m": {"tokens_per_min": 120_000}},
        "live_sessions": [
            {"id": "s1", "title": "big", "last_context": 200_000},
            {"id": "s2", "title": "ordinary", "last_context": 10_000},
        ],
    }
    config = {"thresholds": {"burn_rate_warn_per_min": 50_000, "context_crit": 150_000}}
    messages = notifier.alerts_for(conn, overview, config, now=NOW)

    assert [message.key for message in messages] == ["burn", "s1"]
    assert messages[0].severity == "crit"
    assert "120k" in messages[0].text
    assert "пора /clear" in messages[1].text


def test_alert_is_not_repeated_within_cooldown(conn: Any) -> None:
    overview = {"burn": {"1m": {"tokens_per_min": 120_000}}, "live_sessions": []}
    config = {"thresholds": {"burn_rate_warn_per_min": 50_000}}
    channel = Sent()
    with mock.patch.object(notifier, "Channel", return_value=channel):
        notifier.dispatch(conn, notifier.alerts_for(conn, overview, config, now=NOW), {}, now=NOW)
        again = notifier.alerts_for(conn, overview, config, now=NOW + timedelta(minutes=5))
    assert again == []


def test_digest_text_mentions_cost_and_titles() -> None:
    text = notifier.digest_text(
        {"cost_usd": 0.07, "max_severity": "warn"},
        [{"title": "Close the work line", "severity": "warn"}],
    )
    assert "$0.07" in text
    assert "Close the work line" in text


# --- the pause through the API ----------------------------------------------------


def test_pause_endpoint_holds_and_releases(tmp_path: Path) -> None:
    """The tray button and the window button call the very same endpoint (task D5)."""
    from fastapi.testclient import TestClient

    from cburn.api.server import create_app

    db_path = tmp_path / "api.db"
    connect(db_path).close()
    app = create_app(db_path=db_path, projects_dir=tmp_path, watch=False, liveness=lambda: None)
    client = TestClient(app)

    assert client.get("/api/notify").json()["paused_until"] is None

    until = client.post("/api/notify/pause").json()["paused_until"]
    assert until is not None
    assert client.get("/api/notify").json()["paused_until"] == until

    assert client.post("/api/notify/pause?on=false").json()["paused_until"] is None
    assert client.get("/api/notify").json()["paused_until"] is None


def test_notify_state_shows_what_was_sent(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from cburn.api.server import create_app

    db_path = tmp_path / "api.db"
    conn = connect(db_path)
    channel = Sent()
    with mock.patch.object(notifier, "Channel", return_value=channel):
        notifier.dispatch(conn, [rules.Message("daily", "the day total")], {})
    conn.close()

    app = create_app(db_path=db_path, projects_dir=tmp_path, watch=False, liveness=lambda: None)
    state = TestClient(app).get("/api/notify").json()
    assert state["recent"][0]["kind"] == "daily"
    assert state["recent"][0]["ok"] == 1
