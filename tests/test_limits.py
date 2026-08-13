"""Тесты лимитов подписки: разбор ответа, кэш и отказ по частоте."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cloudo_dash import limits as limits_module
from cloudo_dash.limits import LimitsWatcher, PlanLimits, RateLimited, _normalize, read_from_cache

USAGE_RESPONSE: dict[str, Any] = {
    "five_hour": {"utilization": 48.0, "resets_at": "2026-08-13T16:10:00Z"},
    "seven_day": {"utilization": 36.0, "resets_at": "2026-08-16T23:00:00Z"},
    "limits": [
        {
            "kind": "session",
            "group": "session",
            "percent": 48,
            "severity": "normal",
            "resets_at": "2026-08-13T16:10:00Z",
            "scope": None,
            "is_active": True,
        },
        {
            "kind": "weekly_all",
            "group": "weekly",
            "percent": 36,
            "severity": "normal",
            "resets_at": "2026-08-16T23:00:00Z",
            "scope": None,
            "is_active": False,
        },
        {
            "kind": "weekly_scoped",
            "group": "weekly",
            "percent": 3,
            "severity": "normal",
            "resets_at": "2026-08-16T22:59:59Z",
            "scope": {"model": {"id": None, "display_name": "Fable"}},
            "is_active": False,
        },
    ],
}


def test_normalize_uses_limits_array() -> None:
    rows = _normalize(USAGE_RESPONSE)
    assert [row["kind"] for row in rows] == ["session", "weekly_all", "weekly_scoped"]
    assert rows[0]["percent"] == 48
    # Название модели попадает в подпись — как на экране `/usage`.
    assert rows[2]["label"] == "неделя, модель: Fable"


def test_normalize_falls_back_to_window_fields() -> None:
    """Старый ответ без массива limits: окна собираются из five_hour и seven_day."""
    rows = _normalize({k: v for k, v in USAGE_RESPONSE.items() if k != "limits"})
    assert [(row["kind"], row["percent"]) for row in rows] == [
        ("session", 48.0),
        ("weekly_all", 36.0),
    ]


def test_read_from_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Запасной путь — кэш, который ведёт сам Claude Code."""
    state = tmp_path / ".claude.json"
    state.write_text(
        json.dumps(
            {
                "cachedUsageUtilization": {
                    "fetchedAtMs": 1_786_355_967_206,
                    "utilization": USAGE_RESPONSE,
                }
            }
        )
    )
    monkeypatch.setattr(limits_module, "CLAUDE_STATE", state)

    value = read_from_cache()
    assert value is not None
    assert value.source == "cache"
    assert value.fetched_at == pytest.approx(1_786_355_967.206)
    assert len(value.limits) == 3


def test_read_from_cache_without_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits_module, "CLAUDE_STATE", tmp_path / "нет-файла.json")
    assert read_from_cache() is None


def test_watcher_holds_value_between_refreshes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пока пауза не вышла, эндпоинт не дёргается."""
    calls: list[int] = []

    def fetch() -> PlanLimits:
        calls.append(1)
        return PlanLimits("api", 1.0, "max", "default_claude_max_5x", [{"kind": "session"}])

    monkeypatch.setattr(limits_module, "fetch_from_api", fetch)
    watcher = LimitsWatcher(refresh_seconds=300)

    watcher.current(now=1000.0)
    watcher.current(now=1100.0)
    assert len(calls) == 1

    watcher.current(now=1400.0)  # пауза вышла
    assert len(calls) == 2


def test_watcher_keeps_last_value_on_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отказ по частоте не должен ронять показания к устаревшему кэшу."""
    good = PlanLimits("api", 1.0, "max", None, [{"kind": "session", "percent": 48}])
    state = {"first": True}

    def fetch() -> PlanLimits:
        if state["first"]:
            state["first"] = False
            return good
        raise RateLimited("")

    monkeypatch.setattr(limits_module, "fetch_from_api", fetch)
    monkeypatch.setattr(
        limits_module, "read_from_cache", lambda: PlanLimits("cache", 0.0, None, None, [])
    )
    watcher = LimitsWatcher(refresh_seconds=10)

    assert watcher.current(now=0.0)["source"] == "api"
    after = watcher.current(now=100.0)
    assert after["source"] == "api"  # осталось последнее удачное, а не кэш
    assert after["limits"][0]["percent"] == 48


def test_watcher_waits_longer_after_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fetch() -> PlanLimits:
        calls.append(1)
        raise RateLimited("")

    monkeypatch.setattr(limits_module, "fetch_from_api", fetch)
    monkeypatch.setattr(limits_module, "read_from_cache", lambda: None)
    watcher = LimitsWatcher(refresh_seconds=10)

    watcher.current(now=0.0)
    watcher.current(now=60.0)  # обычная пауза давно вышла, но после 429 ждём дольше
    assert len(calls) == 1


def test_watcher_without_api_uses_cache_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch() -> PlanLimits:  # pragma: no cover — не должен вызываться
        raise AssertionError("сеть не должна трогаться при use_api=False")

    monkeypatch.setattr(limits_module, "fetch_from_api", fetch)
    monkeypatch.setattr(
        limits_module, "read_from_cache", lambda: PlanLimits("cache", 0.0, None, None, [])
    )
    assert LimitsWatcher(use_api=False).current(now=0.0)["source"] == "cache"


def test_watcher_refresh_ignores_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Кнопка в виджете спрашивает лимиты сразу, не дожидаясь паузы."""
    calls: list[int] = []

    def fetch() -> PlanLimits:
        calls.append(1)
        return PlanLimits("api", float(len(calls)), "max", None, [{"kind": "session"}])

    monkeypatch.setattr(limits_module, "fetch_from_api", fetch)
    watcher = LimitsWatcher(refresh_seconds=300)

    watcher.current(now=1000.0)
    watcher.current(now=1010.0)  # пауза не вышла — запроса нет
    assert len(calls) == 1

    assert watcher.refresh(now=1020.0)["fetched_at"] == 2.0
    assert len(calls) == 2
