"""Where a message goes: the bridge, the Bot API or nowhere (task D5, SPEC §7).

The main channel is the neighbouring `cc-tg-bridge`: it already knows topics, buttons and
whom to write to, and all we have to do is send text. The direct Bot API stays as a
fallback for those without the bridge. `off` disables sending entirely - the instrument
keeps counting, it just stays quiet.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

#: How long to wait for telegram: holding the advisor tick longer is pointless, and the
#: message is not important enough to slow everything else down for it.
TIMEOUT = 5.0


class Channel:
    """Sending one text. It does not swallow the error - it returns it in words."""

    def __init__(self, config: dict[str, Any]) -> None:
        telegram = config.get("telegram") or {}
        self.mode: str = telegram.get("mode") or "off"
        self.bridge_url: str = (telegram.get("bridge_url") or "").rstrip("/")
        self.bot_token: str = telegram.get("bot_token") or ""
        self.chat_id: str = str(telegram.get("chat_id") or "")
        # The bridge token lives in its own config: duplicating a secret in two
        # places is not allowed, so we read it where the bridge reads it.
        self.hook_token: str = telegram.get("bridge_token") or _bridge_token()

    @property
    def enabled(self) -> bool:
        return self.mode in {"bridge", "bot"}

    def send(self, text: str, severity: str = "info", silent: bool | None = None) -> str | None:
        """Send; return None on success or a description of the error."""
        if not self.enabled:
            return None
        try:
            if self.mode == "bridge":
                return self._send_bridge(text, severity, silent)
            return self._send_bot(text, severity, silent)
        except Exception as error:  # network, timeout, bad answer
            log.warning("notification did not go out (%s): %s", self.mode, error)
            return str(error)

    def _send_bridge(self, text: str, severity: str, silent: bool | None) -> str | None:
        if not self.bridge_url:
            return "telegram.bridge_url is not set"
        payload: dict[str, Any] = {"text": text, "severity": severity}
        if silent is not None:
            payload["silent"] = silent
        headers = {"Authorization": f"Bearer {self.hook_token}"} if self.hook_token else {}
        response = httpx.post(
            f"{self.bridge_url}/notify", json=payload, headers=headers, timeout=TIMEOUT
        )
        if response.status_code >= 400:
            return f"the bridge answered {response.status_code}: {response.text[:200]}"
        return None

    def _send_bot(self, text: str, severity: str, silent: bool | None) -> str | None:
        if not self.bot_token or not self.chat_id:
            return "telegram.bot_token and telegram.chat_id are not set"
        quiet = silent if silent is not None else severity == "info"
        response = httpx.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": text,
                "disable_notification": quiet,
                "link_preview_options": {"is_disabled": True},
            },
            timeout=TIMEOUT,
        )
        if response.status_code >= 400:
            return f"the Bot API answered {response.status_code}: {response.text[:200]}"
        return None


def _bridge_token() -> str:
    """The bridge token from its own config: `~/.config/cc-tg-bridge/config.json`."""
    import json
    from pathlib import Path

    path = Path.home() / ".config" / "cc-tg-bridge" / "config.json"
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("hookToken") or "")
    except Exception:
        return ""
