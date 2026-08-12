"""Конфиг ~/.config/claude-speedo/config.toml (TZ §8).

Файла может не быть — тогда работают дефолты. Пользовательские значения
накладываются поверх дефолтов посекционно, незнакомые ключи сохраняются.
"""

from __future__ import annotations

import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

import tomli_w

from .paths import CONFIG_PATH

DEFAULTS: dict[str, Any] = {
    "watch": {"include": ["**"], "exclude": []},
    "thresholds": {
        "context_warn": 80_000,
        "context_crit": 150_000,
        "idle_run": 5,
        "burn_rate_warn_per_min": 50_000,
    },
    "analyzer": {
        "enabled": True,
        "interval_minutes": 60,
        "model": "haiku",
        "weekly_deep_model": "sonnet",
        "allow_snippets": False,
    },
    "telegram": {
        "mode": "bridge",
        "bridge_url": "http://localhost:8788/",
        "bot_token": "",
        "chat_id": "",
        "daily_summary_at": "21:00",
    },
    "server": {"port": 8799},
    "prices": {},
}


def load(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Прочитать конфиг, наложив его поверх дефолтов."""
    config = deepcopy(DEFAULTS)
    if not path.exists():
        return config
    with path.open("rb") as fh:
        user = tomllib.load(fh)
    for section, values in user.items():
        if isinstance(values, dict) and isinstance(config.get(section), dict):
            config[section].update(values)
        else:
            config[section] = values
    return config


def save(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    """Записать конфиг (используется экраном «Настройки»)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        tomli_w.dump(config, fh)
