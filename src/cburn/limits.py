"""Subscription limits - the same numbers `/usage` shows in Claude Code.

Claude Code takes them from `GET /api/oauth/usage`, authorising with an OAuth token from
the macOS keychain, and stores the answer in `~/.claude.json` under the key
`cachedUsageUtilization`. We do the same:

* the main path is our own request to that same endpoint, no more often than once every
  few minutes (the request only reads, it changes nothing in the account);
* the fallback is the cache from `~/.claude.json`. It refreshes only when Claude Code
  itself opens `/usage`, and in practice lags by days, so it is served together with the
  time it was obtained, so the dashboard can tell how fresh the data is.

The token is read from the keychain and never written down: not into the database, not
into the log, not into the API answer - only percentages and reset times leave.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from . import paths

log = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

#: The keychain entry where Claude Code keeps the OAuth token.
KEYCHAIN_SERVICE = "Claude Code-credentials"

#: The local answer cache that Claude Code maintains itself. It travels with the Claude
#: Code directory: a demo tree gets the seeded one, the real run gets the real one.
CLAUDE_STATE = paths.CLAUDE_STATE

#: How often to ask for the limits. More often is pointless: percentages move slowly,
#: and extra requests to someone else's endpoint serve no purpose.
REFRESH_SECONDS = 300.0

REQUEST_TIMEOUT = 10.0

#: The limits endpoint rate-limits itself (it answers 429). Having been refused,
#: we wait longer than usual and live on the last known value.
BACKOFF_SECONDS = 900.0

#: The window kinds Anthropic returns - the same ones the `/usage` screen shows.
#: Their captions live in the frontend dictionary: the text belongs to the interface,
#: and the interface has two languages.
KINDS = ("session", "weekly_all", "weekly_scoped")


@dataclass
class PlanLimits:
    """A normalised answer: percentages, reset times and where it came from."""

    source: str  # api | cache | none
    fetched_at: float | None  # unix time it was obtained
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
    """Pull the Claude Code entry out of the macOS keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=REQUEST_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("the keychain is unavailable: %s", exc)
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return dict(json.loads(result.stdout))
    except json.JSONDecodeError:
        log.warning("the keychain entry could not be parsed")
        return None


def _normalize(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Reduce the answer to a list of windows with percentages.

    The main source is the `limits` array; when it is missing (an old answer), the windows
    are assembled from the `five_hour` and `seven_day` fields.
    """
    rows = payload.get("limits")
    if isinstance(rows, list) and rows:
        return [
            {
                "kind": row.get("kind"),
                "model": _model_name(row),
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
            "model": None,
            "percent": window.get("utilization"),
            "resets_at": window.get("resets_at"),
            "severity": None,
            "is_active": True,
        }
        for kind, window in windows
        if isinstance(window, dict) and window.get("utilization") is not None
    ]


def _model_name(row: dict[str, Any]) -> str | None:
    """The model a window is scoped to; the caption around it is built by the frontend."""
    scope = row.get("scope")
    if isinstance(scope, dict):
        model = scope.get("model")
        if isinstance(model, dict) and model.get("display_name"):
            return str(model["display_name"])
    return None


class RateLimited(Exception):
    """The endpoint asked us to wait."""


def fetch_from_api() -> PlanLimits | None:
    """Ask Anthropic for the limits. The token comes from the keychain."""
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
        log.warning("limits were not requested: %s", exc)
        return PlanLimits("none", None, oauth.get("subscriptionType"), None, [], str(exc))
    if response.status_code == 429:
        raise RateLimited(response.headers.get("retry-after", ""))
    if response.status_code != 200:
        # The token may have expired: only Claude Code can refresh it, so we
        # fall back to the cache silently.
        log.info("the limits endpoint answered %s", response.status_code)
        return None
    return PlanLimits(
        source="api",
        fetched_at=time.time(),
        plan=oauth.get("subscriptionType"),
        tier=oauth.get("rateLimitTier"),
        limits=_normalize(response.json()),
    )


def read_from_cache() -> PlanLimits | None:
    """Read the cache that Claude Code maintains itself (read-only)."""
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
    """Holds the last known limits, refreshing them no more often than REFRESH_SECONDS."""

    def __init__(
        self, refresh_seconds: float = REFRESH_SECONDS, use_api: bool | None = None
    ) -> None:
        self.refresh_seconds = refresh_seconds
        # A second instance - the demo tree, a test - must not ask about the real account:
        # its token is in the keychain all the same, and the answer would be real numbers.
        self.use_api = not paths.OVERRIDDEN if use_api is None else use_api
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
                log.info("limits requested too often, waiting %.0f s", wait)
        # The Claude Code cache is a fallback; the last successful value beats it,
        # because that cache refreshes only when Claude Code itself opens /usage.
        if value is None:
            value = (known if known and known.source == "api" else None) or read_from_cache()
        if value is None:
            value = PlanLimits("none", None, None, None, [], "limits are unavailable")

        with self._lock:
            self._value = value
            self._checked_at = moment
            self._wait = wait
        return value.as_dict()

    def refresh(self, now: float | None = None) -> dict[str, Any]:
        """Ask for the limits now, without waiting out the pause: the user pressed it.

        Running into a 429 is possible, and then the last known value stays while the
        next attempt is pushed out by `Retry-After`.
        """
        return self.current(now, force=True)


def _retry_after(header: str) -> float:
    """How long to wait after a 429: from the header, otherwise a fixed pause."""
    try:
        return max(float(header), REFRESH_SECONDS)
    except ValueError:
        return BACKOFF_SECONDS
