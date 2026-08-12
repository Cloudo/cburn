"""CLI `cdash` (TZ §10).

Реализовано: `paths`, `initdb`. Остальные команды объявлены каркасом — они
наполняются по этапам: stats/sessions/session — M1, serve — M2, reindex — M1.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, config, paths
from .db import connect

NOT_IMPLEMENTED_MILESTONE = {
    "stats": "M1",
    "sessions": "M1",
    "session": "M1",
    "reindex": "M1",
    "serve": "M2",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cdash", description="Спидометр Claude")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("paths", help="показать пути конфига, БД и транскриптов")
    sub.add_parser("initdb", help="создать БД и применить схему")
    sub.add_parser("stats", help="сводка расхода за период")
    sub.add_parser("sessions", help="список сессий")
    session = sub.add_parser("session", help="детали одной сессии")
    session.add_argument("session_id")
    sub.add_parser("reindex", help="полная переиндексация истории с нуля")
    sub.add_parser("serve", help="запустить API-сервер и дашборд")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "paths":
        cfg = config.load()
        print(f"транскрипты : {paths.CLAUDE_PROJECTS_DIR} (read-only)")
        print(f"конфиг      : {paths.CONFIG_PATH}")
        print(f"БД          : {paths.DB_PATH}")
        print(f"порт        : {cfg['server']['port']}")
        return 0

    if args.command == "initdb":
        with connect() as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
        print(f"{paths.DB_PATH}: {', '.join(tables)}")
        return 0

    milestone = NOT_IMPLEMENTED_MILESTONE.get(args.command, "?")
    print(f"команда `{args.command}` ещё не реализована (этап {milestone})", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
