"""Пути, которыми пользуется приложение. Единственное место, где они задаются."""

from __future__ import annotations

import os
from pathlib import Path

#: Каталог транскриптов Claude Code. Открывается ТОЛЬКО на чтение (TZ §2).
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"

#: Постоянные инструкции: едут в каждый запрос, поэтому их объём — метрика.
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"

CONFIG_PATH = Path.home() / ".config" / "cburn" / "config.toml"
DATA_DIR = Path.home() / ".local" / "share" / "cburn"
DB_PATH = DATA_DIR / "cburn.db"

#: Прежнее имя проекта. Каталоги переезжают сами: база копилась месяцами.
LEGACY_NAME = "cloudo-dash"
LEGACY_CONFIG_DIR = Path.home() / ".config" / LEGACY_NAME
LEGACY_DATA_DIR = Path.home() / ".local" / "share" / LEGACY_NAME


def migrate_legacy() -> None:
    """Перенести состояние из каталогов прежнего имени, если новых ещё нет."""
    for legacy, current in ((LEGACY_DATA_DIR, DATA_DIR), (LEGACY_CONFIG_DIR, CONFIG_PATH.parent)):
        if current.exists() or not legacy.is_dir():
            continue
        try:
            current.parent.mkdir(parents=True, exist_ok=True)
            legacy.rename(current)
        except OSError:
            return  # переезд не удался, работаем на новых, пустых каталогах
    # Файлы базы (вместе с -wal и -shm) названы по проекту, а не по каталогу.
    for old in DATA_DIR.glob(f"{LEGACY_NAME}.db*"):
        target = DB_PATH.with_name(old.name.replace(f"{LEGACY_NAME}.db", DB_PATH.name, 1))
        if not target.exists():
            try:
                old.rename(target)
            except OSError:
                pass


def ensure_dirs() -> None:
    """Создать каталоги приложения. Каталог Claude Code при этом не трогается."""
    migrate_legacy()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
