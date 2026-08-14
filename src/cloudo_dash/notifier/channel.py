"""Куда уходит сообщение: бридж, Bot API или никуда (задача D5, ТЗ §7).

Основной канал — соседний `cc-tg-bridge`: он уже умеет темы, кнопки и знает,
кому писать, а нам достаточно послать текст. Прямой Bot API остаётся запасным
для тех, у кого бриджа нет. `off` выключает отправку целиком — прибор
продолжает считать, просто молчит.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

#: Сколько ждать телеграм: дольше держать такт советчика незачем, а сообщение
#: не настолько важно, чтобы ради него тормозить всё остальное.
TIMEOUT = 5.0


class Channel:
    """Отправка одного текста. Ошибку не глотает — возвращает её словами."""

    def __init__(self, config: dict[str, Any]) -> None:
        telegram = config.get("telegram") or {}
        self.mode: str = telegram.get("mode") or "off"
        self.bridge_url: str = (telegram.get("bridge_url") or "").rstrip("/")
        self.bot_token: str = telegram.get("bot_token") or ""
        self.chat_id: str = str(telegram.get("chat_id") or "")
        # Токен бриджа лежит в его собственном конфиге: дублировать секрет
        # в двух местах нельзя, поэтому читаем оттуда же, откуда читает он.
        self.hook_token: str = telegram.get("bridge_token") or _bridge_token()

    @property
    def enabled(self) -> bool:
        return self.mode in {"bridge", "bot"}

    def send(self, text: str, severity: str = "info", silent: bool | None = None) -> str | None:
        """Отправить; вернуть None при успехе или описание ошибки."""
        if not self.enabled:
            return None
        try:
            if self.mode == "bridge":
                return self._send_bridge(text, severity, silent)
            return self._send_bot(text, severity, silent)
        except Exception as error:  # сеть, таймаут, неверный ответ
            log.warning("уведомление не ушло (%s): %s", self.mode, error)
            return str(error)

    def _send_bridge(self, text: str, severity: str, silent: bool | None) -> str | None:
        if not self.bridge_url:
            return "не задан telegram.bridge_url"
        payload: dict[str, Any] = {"text": text, "severity": severity}
        if silent is not None:
            payload["silent"] = silent
        headers = {"Authorization": f"Bearer {self.hook_token}"} if self.hook_token else {}
        response = httpx.post(
            f"{self.bridge_url}/notify", json=payload, headers=headers, timeout=TIMEOUT
        )
        if response.status_code >= 400:
            return f"бридж ответил {response.status_code}: {response.text[:200]}"
        return None

    def _send_bot(self, text: str, severity: str, silent: bool | None) -> str | None:
        if not self.bot_token or not self.chat_id:
            return "не заданы telegram.bot_token и chat_id"
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
            return f"Bot API ответил {response.status_code}: {response.text[:200]}"
        return None


def _bridge_token() -> str:
    """Токен бриджа из его конфига: `~/.config/cc-tg-bridge/config.json`."""
    import json
    from pathlib import Path

    path = Path.home() / ".config" / "cc-tg-bridge" / "config.json"
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("hookToken") or "")
    except Exception:
        return ""
