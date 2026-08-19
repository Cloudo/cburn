"""Paths the application uses. The single place where they are defined."""

from __future__ import annotations

import os
from pathlib import Path

#: Claude Code transcript directory. Opened READ-ONLY (SPEC §2).
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"

#: Persistent instructions: they ride along with every request, so their size is a metric.
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"

#: A second instance next to the real one (the demo dataset, tests): the config and the
#: data directory move by environment, and the real directories stay untouched.
_OVERRIDDEN = "CBURN_CONFIG" in os.environ or "CBURN_DATA_DIR" in os.environ

CONFIG_PATH = Path(
    os.environ.get("CBURN_CONFIG", Path.home() / ".config" / "cburn" / "config.toml")
)
DATA_DIR = Path(os.environ.get("CBURN_DATA_DIR", Path.home() / ".local" / "share" / "cburn"))
DB_PATH = DATA_DIR / "cburn.db"

#: The browser's choice for the native surfaces: the tray cannot read `localStorage`.
UI_STATE_PATH = DATA_DIR / "ui.json"

#: Former project name. The directories move themselves: the database took months to fill.
LEGACY_NAME = "cloudo-dash"
LEGACY_CONFIG_DIR = Path.home() / ".config" / LEGACY_NAME
LEGACY_DATA_DIR = Path.home() / ".local" / "share" / LEGACY_NAME


def migrate_legacy() -> None:
    """Move state over from the directories of the former name, unless the new ones exist."""
    if _OVERRIDDEN:
        return  # a second instance must not drag the real state into its directories
    for legacy, current in ((LEGACY_DATA_DIR, DATA_DIR), (LEGACY_CONFIG_DIR, CONFIG_PATH.parent)):
        if current.exists() or not legacy.is_dir():
            continue
        try:
            current.parent.mkdir(parents=True, exist_ok=True)
            legacy.rename(current)
        except OSError:
            return  # the move failed, we work on the new, empty directories
    # Database files (together with -wal and -shm) are named after the project, not the directory.
    for old in DATA_DIR.glob(f"{LEGACY_NAME}.db*"):
        target = DB_PATH.with_name(old.name.replace(f"{LEGACY_NAME}.db", DB_PATH.name, 1))
        if not target.exists():
            try:
                old.rename(target)
            except OSError:
                pass


def ensure_dirs() -> None:
    """Create the application directories. The Claude Code directory is left alone."""
    migrate_legacy()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
