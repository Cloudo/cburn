"""Лимиты подписки — те же цифры, что показывает `/usage` в Claude Code.

Claude Code берёт их из `GET /api/oauth/usage`, авторизуясь OAuth-токеном из
связки ключей macOS, и кладёт ответ в `~/.claude.json` под ключом
`cachedUsageUtilization`. Мы делаем то же самое:

* основной путь — свой запрос к тому же эндпоинту, не чаще раза в несколько
  минут (запрос только читает, ничего в аккаунте не меняет);
* запасной — кэш из `~/.claude.json`. Он обновляется, только когда сам Claude
  Code открывает `/usage`, и на практике отстаёт на дни, поэтому отдаётся с
  временем получения, чтобы дашборд мог сказать, насколько данные свежие.

Токен читается из связки ключей и никуда не записывается: ни в БД, ни в лог,
ни в ответ API — наружу уходят только проценты и время сброса.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

#: Запись связки ключей, в которой Claude Code хранит OAuth-токен.
KEYCHAIN_SERVICE = "Claude Code-credentials"

#: Локальный кэш ответа, который ведёт сам Claude Code.
CLAUDE_STATE = Path.home() / ".claude.json"

#: Как часто спрашивать лимиты. Чаще незачем: проценты меняются медленно,
#: а лишние запросы к чужому эндпоинту ни к чему.
REFRESH_SECONDS = 300.0

REQUEST_TIMEOUT = 10.0

#: Эндпоинт лимитов сам ограничивает частоту (отвечает 429). Получив отказ,
#: ждём дольше обычного и живём на последнем известном значении.
BACKOFF_SECONDS = 900.0

#: Человеческие названия окон — те же, что на экране `/usage`.
KIND_LABELS = {
    "session": "текущая сессия",
    "weekly_all": "неделя, все модели",
    "weekly_scoped": "неделя, модель",
}


@dataclass
class PlanLimits:
    """Нормализованный ответ: проценты, время сброса и откуда взято."""

    source: str  # api | cache | none
    fetched_at: float | None  # unix-время получения
    plan: str | None  # max, pro…
    tier: str | None  # default_claude_max_5x…
    limits: list[dict[str, Any]]
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "fetched_at": self.fetched_at,
            "plan": self.plan,
            "tier": self.tier,
            "limits": self.limits,
            "error": self.error,
        }


def _keychain_credentials() -> dict[str, Any] | None:
    """Достать запись Claude Code из связки ключей macOS."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=REQUEST_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("связка ключей недоступна: %s", exc)
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return dict(json.loads(result.stdout))
    except json.JSONDecodeError:
        log.warning("запись связки ключей не разобрана")
        return None


def _normalize(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Привести ответ к списку окон с процентами.

    Основной источник — массив `limits`; если его нет (старый ответ), окна
    собираются из полей `five_hour` и `seven_day`.
    """
    rows = payload.get("limits")
    if isinstance(rows, list) and rows:
        return [
            {
                "kind": row.get("kind"),
                "label": _label(row),
                "percent": row.get("percent"),
                "resets_at": row.get("resets_at"),
                "severity": row.get("severity"),
                "is_active": bool(row.get("is_active")),
            }
            for row in rows
            if isinstance(row, dict) and row.get("percent") is not None
        ]
    windows = [("session", payload.get("five_hour")), ("weekly_all", payload.get("seven_day"))]
    return [
        {
            "kind": kind,
            "label": KIND_LABELS.get(kind, kind),
            "percent": window.get("utilization"),
            "resets_at": window.get("resets_at"),
            "severity": None,
            "is_active": True,
        }
        for kind, window in windows
        if isinstance(window, dict) and window.get("utilization") is not None
    ]


def _label(row: dict[str, Any]) -> str:
    base = KIND_LABELS.get(str(row.get("kind")), str(row.get("kind")))
    scope = row.get("scope")
    if isinstance(scope, dict):
        model = scope.get("model")
        if isinstance(model, dict) and model.get("display_name"):
            return f"{base}: {model['display_name']}"
    return base


class RateLimited(Exception):
    """Эндпоинт попросил подождать."""


def fetch_from_api() -> PlanLimits | None:
    """Спросить лимиты у Anthropic. Токен берётся из связки ключей."""
    credentials = _keychain_credentials()
    oauth = (credentials or {}).get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    if not token:
        return None
    try:
        response = httpx.get(
            USAGE_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        log.warning("лимиты не запрошены: %s", exc)
        return PlanLimits("none", None, oauth.get("subscriptionType"), None, [], str(exc))
    if response.status_code == 429:
        raise RateLimited(response.headers.get("retry-after", ""))
    if response.status_code != 200:
        # Токен мог истечь: обновлять его умеет только Claude Code, поэтому
        # молча уходим на кэш.
        log.info("эндпоинт лимитов ответил %s", response.status_code)
        return None
    return PlanLimits(
        source="api",
        fetched_at=time.time(),
        plan=oauth.get("subscriptionType"),
        tier=oauth.get("rateLimitTier"),
        limits=_normalize(response.json()),
    )


def read_from_cache() -> PlanLimits | None:
    """Прочитать кэш, который ведёт сам Claude Code (только чтение)."""
    try:
        state = json.loads(CLAUDE_STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    cached = state.get("cachedUsageUtilization")
    if not isinstance(cached, dict):
        return None
    payload = cached.get("utilization")
    if not isinstance(payload, dict):
        return None
    fetched_ms = cached.get("fetchedAtMs")
    return PlanLimits(
        source="cache",
        fetched_at=float(fetched_ms) / 1000 if isinstance(fetched_ms, int | float) else None,
        plan=None,
        tier=None,
        limits=_normalize(payload),
    )


class LimitsWatcher:
    """Держит последние известные лимиты, обновляя их не чаще REFRESH_SECONDS."""

    def __init__(self, refresh_seconds: float = REFRESH_SECONDS, use_api: bool = True) -> None:
        self.refresh_seconds = refresh_seconds
        self.use_api = use_api
        self._lock = threading.Lock()
        self._value: PlanLimits | None = None
        self._checked_at = 0.0
        self._wait = refresh_seconds

    def current(self, now: float | None = None, *, force: bool = False) -> dict[str, Any]:
        moment = time.monotonic() if now is None else now
        with self._lock:
            known = self._value
            if not force and known is not None and moment - self._checked_at < self._wait:
                return known.as_dict()

        value: PlanLimits | None = None
        wait = self.refresh_seconds
        if self.use_api:
            try:
                value = fetch_from_api()
            except RateLimited as limited:
                wait = _retry_after(str(limited))
                log.info("лимиты запрошены слишком часто, ждём %.0f с", wait)
        # Кэш Claude Code — запасной путь; последнее удачное значение лучше него,
        # потому что кэш обновляется, только когда сам Claude Code открывает /usage.
        if value is None:
            value = (known if known and known.source == "api" else None) or read_from_cache()
        if value is None:
            value = PlanLimits("none", None, None, None, [], "лимиты недоступны")

        with self._lock:
            self._value = value
            self._checked_at = moment
            self._wait = wait
        return value.as_dict()

    def refresh(self, now: float | None = None) -> dict[str, Any]:
        """Спросить лимиты сейчас, не дожидаясь паузы: пользователь нажал сам.

        Нарваться на 429 при этом можно, и тогда останется последнее известное
        значение, а следующая попытка отодвинется на `Retry-After`.
        """
        return self.current(now, force=True)


def _retry_after(header: str) -> float:
    """Сколько ждать после 429: по заголовку, иначе — фиксированная пауза."""
    try:
        return max(float(header), REFRESH_SECONDS)
    except ValueError:
        return BACKOFF_SECONDS
