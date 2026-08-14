"""Тесты уведомлений (задача D5, ТЗ §7).

Сеть здесь не поднимается: канал подменяется, а правила проверяются как чистые
функции — они и написаны так, чтобы это было возможно.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from cloudo_dash import notifier
from cloudo_dash.db import connect
from cloudo_dash.notifier import rules

NOW = datetime(2026, 8, 14, 18, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path: Path) -> Any:
    connection = connect(tmp_path / "notify.db")
    yield connection
    connection.close()


# --- правила -----------------------------------------------------------------


def test_digest_goes_only_when_there_is_something_to_say() -> None:
    """Часовая выжимка без важных советов — это спам, а не польза."""
    assert rules.digest_message("info", "текст") is None
    assert rules.digest_message(None, "текст") is None
    assert rules.digest_message("warn", "текст").kind == "digest"
    assert rules.digest_message("crit", "текст").severity == "crit"


def test_daily_summary_goes_once_a_day() -> None:
    """Сводка уходит после назначенного часа и ровно один раз."""
    morning = datetime(2026, 8, 14, 9, 0).astimezone()
    evening = datetime(2026, 8, 14, 21, 30).astimezone()

    assert rules.daily_message(morning, "21:00", None, "итог") is None
    first = rules.daily_message(evening, "21:00", None, "итог")
    assert first is not None and first.kind == "daily"
    # Уже отправляли сегодня после срока — второй раз не шлём.
    assert rules.daily_message(evening, "21:00", evening, "итог") is None
    # Вчерашняя отправка сегодняшнюю не отменяет.
    assert rules.daily_message(evening, "21:00", evening - timedelta(days=1), "итог") is not None


def test_daily_summary_survives_broken_time() -> None:
    """Кривое время в конфиге не должно ронять такт."""
    assert rules.daily_message(NOW, "не время", None, "итог") is None


def test_alerts_respect_cooldown() -> None:
    """Одна сессия не будит чаще, чем раз в полчаса."""
    candidates = [("s1", "warn", "контекст 160k")]
    assert rules.alert_messages(NOW, candidates, {}) != []
    assert rules.alert_messages(NOW, candidates, {"s1": NOW - timedelta(minutes=10)}) == []
    assert rules.alert_messages(NOW, candidates, {"s1": NOW - timedelta(minutes=31)}) != []


def test_pause_lets_crit_through() -> None:
    """Пауза — это «не отвлекай по мелочам», а не «выключи прибор»."""
    quiet = NOW + timedelta(hours=1)
    assert rules.allowed(rules.Message("alert", "…", "warn"), quiet, NOW) is False
    assert rules.allowed(rules.Message("alert", "…", "crit"), quiet, NOW) is True
    assert rules.allowed(rules.Message("alert", "…", "warn"), None, NOW) is True


# --- отправка и память -------------------------------------------------------


class Sent:
    """Канал, который никуда не ходит, а запоминает отправленное."""

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
        sent = notifier.dispatch(conn, [rules.Message("alert", "горит", "crit", "s1")], {})
    assert sent == 1
    assert channel.messages == [("crit", "горит")]
    row = conn.execute("SELECT kind, key, severity, ok FROM notifications").fetchone()
    assert (row["kind"], row["key"], row["severity"], row["ok"]) == ("alert", "s1", "crit", 1)


def test_failed_send_is_remembered_as_failed(conn: Any) -> None:
    """Неудачу видно в истории: иначе cooldown промолчит о том, что не дошло."""
    channel = Sent(error="бридж ответил 500")
    with mock.patch.object(notifier, "Channel", return_value=channel):
        sent = notifier.dispatch(conn, [rules.Message("alert", "горит", "warn", "s1")], {})
    assert sent == 0
    assert conn.execute("SELECT ok FROM notifications").fetchone()[0] == 0


def test_pause_holds_everything_but_crit(conn: Any) -> None:
    notifier.set_pause(conn, NOW + timedelta(hours=1))
    channel = Sent()
    with mock.patch.object(notifier, "Channel", return_value=channel):
        notifier.dispatch(
            conn,
            [
                rules.Message("alert", "мелочь", "warn", "s1"),
                rules.Message("alert", "горит", "crit", "burn"),
            ],
            {},
            now=NOW,
        )
    assert [severity for severity, _ in channel.messages] == ["crit"]


def test_off_channel_sends_nothing(conn: Any) -> None:
    """Канал `off` выключает отправку, но прибор продолжает считать."""
    channel = Sent(mode="off")
    with mock.patch.object(notifier, "Channel", return_value=channel):
        assert notifier.dispatch(conn, [rules.Message("daily", "итог")], {}) == 0
    assert channel.messages == []
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0


def test_alerts_come_from_the_same_numbers_as_the_dashboard(conn: Any) -> None:
    """Тревога не считает сама: пороги и цифры те же, что на экране."""
    overview = {
        "burn": {"1m": {"tokens_per_min": 120_000}},
        "live_sessions": [
            {"id": "s1", "title": "большая", "last_context": 200_000},
            {"id": "s2", "title": "обычная", "last_context": 10_000},
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
        [{"title": "Закрыть линию работы", "severity": "warn"}],
    )
    assert "$0.07" in text
    assert "Закрыть линию работы" in text


# --- пауза через API ---------------------------------------------------------


def test_pause_endpoint_holds_and_releases(tmp_path: Path) -> None:
    """Кнопка в трее и в окне зовёт один и тот же эндпоинт (задача D5)."""
    from fastapi.testclient import TestClient

    from cloudo_dash.api.server import create_app

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

    from cloudo_dash.api.server import create_app

    db_path = tmp_path / "api.db"
    conn = connect(db_path)
    channel = Sent()
    with mock.patch.object(notifier, "Channel", return_value=channel):
        notifier.dispatch(conn, [rules.Message("daily", "итог дня")], {})
    conn.close()

    app = create_app(db_path=db_path, projects_dir=tmp_path, watch=False, liveness=lambda: None)
    state = TestClient(app).get("/api/notify").json()
    assert state["recent"][0]["kind"] == "daily"
    assert state["recent"][0]["ok"] == 1
