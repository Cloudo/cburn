"""Пути, которыми пользуется приложение. Единственное место, где они задаются."""

from __future__ import annotations

import os
from pathlib import Path

#: Каталог транскриптов Claude Code. Открывается ТОЛЬКО на чтение (TZ §2).
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"

#: Постоянные инструкции: едут в каждый запрос, поэтому их объём — метрика.
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"

CONFIG_PATH = Path.home() / ".config" / "cloudo-dash" / "config.toml"
DATA_DIR = Path.home() / ".local" / "share" / "cloudo-dash"
DB_PATH = DATA_DIR / "cloudo-dash.db"


def ensure_dirs() -> None:
    """Создать каталоги приложения. Каталог Claude Code при этом не трогается."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
