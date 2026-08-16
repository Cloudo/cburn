"""Config ~/.config/cburn/config.toml (TZ §8).

The file may be missing - then the defaults apply. User values are layered
over the defaults section by section, unknown keys are kept.
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
        # In which language the advisor answers. The prompt itself is English; the tips are
        # read by a human, so the language of the answer is their choice.
        "language": "en",
    },
    "telegram": {
        "mode": "bridge",
        "bridge_url": "http://localhost:8788/",
        "bot_token": "",
        "chat_id": "",
        "daily_summary_at": "21:00",
    },
    "server": {"port": 8799},
    # Receiving official Claude Code telemetry (milestone E). The flag alone
    # switches nothing on: telemetry is set by Claude Code's environment, see `cburn otel`.
    # Events arrive in bunches on every turn and every tool call, around
    # 400 bytes each, so they have their own retention; 0 means keep everything.
    "otel": {"enabled": True, "keep_days": 30},
    # Carrying tips out (task D7). Switched off, the advisor still returns actions but the
    # dashboard refuses to apply them: it is the single switch for writes into a foreign
    # config, and one is easier to trust than a list of exceptions.
    "actions": {"enabled": True},
    "prices": {},
}


def load(path: Path | None = None) -> dict[str, Any]:
    """Read the config, layering it over the defaults.

    The path is taken from `paths` at call time, not at import: otherwise it could not
    be swapped in tests or by a second configuration - `db.connect` is built
    exactly the same way.
    """
    path = path or paths.CONFIG_PATH
    if path == paths.CONFIG_PATH and not path.exists():
        paths.migrate_legacy()  # the config may still sit under the former project name
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


#: What the "Settings" screen may edit and within which bounds (task C3).
#: A threshold must not be negative, a port must stay inside the user range,
#: and the advisor model must be a known word: such a config breaks work most quietly.
NUMERIC_LIMITS: dict[tuple[str, str], tuple[float, float]] = {
    ("thresholds", "context_warn"): (1_000, 10_000_000),
    ("thresholds", "context_crit"): (1_000, 10_000_000),
    ("thresholds", "idle_run"): (1, 1_000),
    ("thresholds", "burn_rate_warn_per_min"): (100, 100_000_000),
    ("analyzer", "interval_minutes"): (5, 24 * 60),
    ("server", "port"): (1_024, 65_535),
    # Zero means never prune; the upper bound just cuts off typos.
    ("otel", "keep_days"): (0, 3_650),
}

ANALYZER_MODELS = {"haiku", "sonnet", "opus"}

#: Languages the advisor may answer in; the prompt itself is always English.
ANALYZER_LANGUAGES = {"en", "ru"}

TELEGRAM_MODES = {"bridge", "bot", "off"}

#: Price columns: the same four parts of the spend as in `model_prices`.
PRICE_KEYS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")


def validate(config: dict[str, Any]) -> list[str]:
    """Check the config before writing; return a list of human-readable errors."""
    errors: list[str] = []
    for (section, key), (low, high) in NUMERIC_LIMITS.items():
        value = (config.get(section) or {}).get(key)
        if value is None:
            continue
        if not isinstance(value, int | float) or isinstance(value, bool):
            errors.append(f"{section}.{key}: expected a number")
        elif not low <= value <= high:
            errors.append(f"{section}.{key}: expected a number from {low:g} to {high:g}")

    thresholds = config.get("thresholds") or {}
    warn, crit = thresholds.get("context_warn"), thresholds.get("context_crit")
    if isinstance(warn, int | float) and isinstance(crit, int | float) and warn >= crit:
        errors.append("thresholds: the yellow zone must start before the red one")

    analyzer = config.get("analyzer") or {}
    if analyzer.get("model") not in ANALYZER_MODELS | {None}:
        errors.append(f"analyzer.model: expected one of {', '.join(sorted(ANALYZER_MODELS))}")
    if analyzer.get("weekly_deep_model") not in ANALYZER_MODELS | {None}:
        errors.append(
            "analyzer.weekly_deep_model: " + f"expected one of {', '.join(sorted(ANALYZER_MODELS))}"
        )
    if analyzer.get("language") not in ANALYZER_LANGUAGES | {None}:
        errors.append(f"analyzer.language: expected one of {', '.join(sorted(ANALYZER_LANGUAGES))}")

    telegram = config.get("telegram") or {}
    if telegram.get("mode") not in TELEGRAM_MODES | {None}:
        errors.append(f"telegram.mode: expected one of {', '.join(sorted(TELEGRAM_MODES))}")
    daily = telegram.get("daily_summary_at")
    if daily is not None and not _is_time(daily):
        errors.append("telegram.daily_summary_at: expected a time like 21:00")

    for section in ("otel", "actions"):
        enabled = (config.get(section) or {}).get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            errors.append(f"{section}.enabled: expected true or false")

    for model, price in (config.get("prices") or {}).items():
        if not isinstance(price, dict):
            errors.append(f"prices.{model}: expected a table of prices")
            continue
        for key, value in price.items():
            if key not in PRICE_KEYS:
                errors.append(f"prices.{model}: unknown column {key}")
            elif not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
                errors.append(f"prices.{model}.{key}: expected a non-negative number")
    return errors


def _is_time(value: Any) -> bool:
    if not isinstance(value, str) or ":" not in value:
        return False
    hours, _, minutes = value.partition(":")
    return (
        hours.isdigit() and minutes.isdigit() and 0 <= int(hours) <= 23 and 0 <= int(minutes) <= 59
    )


def save(config: dict[str, Any], path: Path | None = None) -> None:
    """Write the config (the "Settings" screen). The path is resolved as in `load`, at call time."""
    path = path or paths.CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        tomli_w.dump(config, fh)
