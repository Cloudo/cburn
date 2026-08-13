"""Конфиг ~/.config/cloudo-dash/config.toml (TZ §8).

Файла может не быть — тогда работают дефолты. Пользовательские значения
накладываются поверх дефолтов посекционно, незнакомые ключи сохраняются.
"""

from __future__ import annotations

import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

import tomli_w

from . import paths

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


def load(path: Path | None = None) -> dict[str, Any]:
    """Прочитать конфиг, наложив его поверх дефолтов.

    Путь берётся из `paths` в момент вызова, а не при импорте: иначе его не
    подменить ни в тестах, ни второй конфигурацией — ровно так же устроен
    `db.connect`.
    """
    path = path or paths.CONFIG_PATH
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


#: Что и в каких границах разрешено править экраном «Настройки» (задача C3).
#: Порог не должен быть отрицательным, порт — вне пользовательского диапазона,
#: а модель советчика — незнакомым словом: такой конфиг тише всего ломает работу.
NUMERIC_LIMITS: dict[tuple[str, str], tuple[float, float]] = {
    ("thresholds", "context_warn"): (1_000, 10_000_000),
    ("thresholds", "context_crit"): (1_000, 10_000_000),
    ("thresholds", "idle_run"): (1, 1_000),
    ("thresholds", "burn_rate_warn_per_min"): (100, 100_000_000),
    ("analyzer", "interval_minutes"): (5, 24 * 60),
    ("server", "port"): (1_024, 65_535),
}

ANALYZER_MODELS = {"haiku", "sonnet", "opus"}
TELEGRAM_MODES = {"bridge", "bot", "off"}

#: Колонки прайса: те же четыре составляющих расхода, что и в `model_prices`.
PRICE_KEYS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")


def validate(config: dict[str, Any]) -> list[str]:
    """Проверить конфиг перед записью; вернуть список понятных человеку ошибок."""
    errors: list[str] = []
    for (section, key), (low, high) in NUMERIC_LIMITS.items():
        value = (config.get(section) or {}).get(key)
        if value is None:
            continue
        if not isinstance(value, int | float) or isinstance(value, bool):
            errors.append(f"{section}.{key}: ждём число")
        elif not low <= value <= high:
            errors.append(f"{section}.{key}: ждём число от {low:g} до {high:g}")

    thresholds = config.get("thresholds") or {}
    warn, crit = thresholds.get("context_warn"), thresholds.get("context_crit")
    if isinstance(warn, int | float) and isinstance(crit, int | float) and warn >= crit:
        errors.append("thresholds: жёлтая зона должна начинаться раньше красной")

    analyzer = config.get("analyzer") or {}
    if analyzer.get("model") not in ANALYZER_MODELS | {None}:
        errors.append(f"analyzer.model: ждём одно из {', '.join(sorted(ANALYZER_MODELS))}")
    if analyzer.get("weekly_deep_model") not in ANALYZER_MODELS | {None}:
        errors.append(
            "analyzer.weekly_deep_model: " + f"ждём одно из {', '.join(sorted(ANALYZER_MODELS))}"
        )

    telegram = config.get("telegram") or {}
    if telegram.get("mode") not in TELEGRAM_MODES | {None}:
        errors.append(f"telegram.mode: ждём одно из {', '.join(sorted(TELEGRAM_MODES))}")
    daily = telegram.get("daily_summary_at")
    if daily is not None and not _is_time(daily):
        errors.append("telegram.daily_summary_at: ждём время вида 21:00")

    for model, price in (config.get("prices") or {}).items():
        if not isinstance(price, dict):
            errors.append(f"prices.{model}: ждём таблицу с ценами")
            continue
        for key, value in price.items():
            if key not in PRICE_KEYS:
                errors.append(f"prices.{model}: незнакомая колонка {key}")
            elif not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
                errors.append(f"prices.{model}.{key}: ждём неотрицательное число")
    return errors


def _is_time(value: Any) -> bool:
    if not isinstance(value, str) or ":" not in value:
        return False
    hours, _, minutes = value.partition(":")
    return (
        hours.isdigit() and minutes.isdigit() and 0 <= int(hours) <= 23 and 0 <= int(minutes) <= 59
    )


def save(config: dict[str, Any], path: Path | None = None) -> None:
    """Записать конфиг (экран «Настройки»). Путь — как в `load`, на момент вызова."""
    path = path or paths.CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        tomli_w.dump(config, fh)
