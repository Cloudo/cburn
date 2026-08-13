"""CLI `cdash` (TZ §10).

Реализовано: `paths`, `initdb`, `reindex`, `prices`, `sessions`, `session`, `serve`.
Фильтры по проекту и периоду и команда `stats` — задача B7.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__, config, paths, pricing
from .collector.indexer import ingest_tree
from .db import connect
from .metrics import recent_sessions, session_models, session_summary, session_tools

NOT_IMPLEMENTED_MILESTONE = {
    "stats": "M1",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cdash", description="Спидометр Claude")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("paths", help="показать пути конфига, БД и транскриптов")
    sub.add_parser("initdb", help="создать БД и применить схему")
    sub.add_parser("stats", help="сводка расхода за период")
    sessions = sub.add_parser("sessions", help="список сессий")
    sessions.add_argument("-n", "--limit", type=int, default=20, help="сколько показать")
    session = sub.add_parser("session", help="детали одной сессии")
    session.add_argument("session_id", help="полный id или его начало")
    sub.add_parser("reindex", help="дочитать транскрипты в БД")
    prices = sub.add_parser("prices", help="применить цены из конфига и пересчитать стоимость")
    prices.add_argument(
        "--init", action="store_true", help="записать в конфиг заготовку тарифов, если их нет"
    )
    serve = sub.add_parser("serve", help="запустить API-сервер и дашборд")
    serve.add_argument("--port", type=int, help="порт (по умолчанию из конфига)")
    serve.add_argument("--host", default="127.0.0.1", help="только localhost, TZ §7")
    serve.add_argument("--reload", action="store_true", help="перезапуск при правках кода")
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

    if args.command == "reindex":
        return _reindex()

    if args.command == "prices":
        return _prices(args.init)

    if args.command == "sessions":
        return _sessions(args.limit)

    if args.command == "session":
        return _session(args.session_id)

    if args.command == "serve":
        return _serve(args.host, args.port, args.reload)

    milestone = NOT_IMPLEMENTED_MILESTONE.get(args.command, "?")
    print(f"команда `{args.command}` ещё не реализована (этап {milestone})", file=sys.stderr)
    return 2


def _serve(host: str, port: int | None, reload: bool) -> int:
    """Поднять API вместе с watcher в одном процессе."""
    import uvicorn

    from .api.server import create_app

    cfg = config.load()
    bind_port = port or int(cfg["server"]["port"])
    print(f"cloudo-dash: http://{host}:{bind_port}  (Ctrl+C — остановить)")
    uvicorn.run(
        "cloudo_dash.api.server:create_app" if reload else create_app(),
        host=host,
        port=bind_port,
        factory=reload,
        reload=reload,
        log_level="warning",
    )
    return 0


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _reindex() -> int:
    """Дочитать все транскрипты (задача B2)."""
    started = time.monotonic()
    with connect() as conn:
        pricing.sync_prices(conn, config.load())
        results = ingest_tree(conn, paths.CLAUDE_PROJECTS_DIR, on_file=_progress)
    if sys.stderr.isatty():
        print(file=sys.stderr)  # закрыть строку прогресса
    lines = sum(result.lines for result in results)
    turns = sum(result.turns_new for result in results)
    known = sum(result.turns_known for result in results)
    elapsed = time.monotonic() - started
    print(f"файлов: {len(results)}, прочитано строк: {_thousands(lines)}, за {elapsed:.1f} с")
    print(f"новых ходов: {_thousands(turns)}, уже известных: {_thousands(known)}")
    return 0


def _progress(done: int, total: int, path: Path) -> None:
    """Живая строка обхода. В пайп не пишется: там нужен только итог."""
    if not sys.stderr.isatty():
        return
    print(f"\r{done}/{total}  {path.name[:36]:<36}", end="", file=sys.stderr, flush=True)


def _prices(init: bool) -> int:
    """Применить `[prices]` из конфига ко всей истории."""
    cfg = config.load()
    if init and not cfg["prices"]:
        cfg["prices"] = pricing.sample_prices()
        config.save(cfg)
        print(f"заготовка тарифов записана в {paths.CONFIG_PATH} — проверьте цены")
    with connect() as conn:
        models = pricing.recalculate(conn, cfg)
        rows = pricing.known_prices(conn)
        unknown = pricing.unknown_models(conn)
        total = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM turns").fetchone()[0]
    if not models:
        print("цен нет: заполните секцию [prices] в конфиге или запустите `cdash prices --init`")
        return 1
    print("модель                вход   выход   кэш 5m   кэш 1h   чтение  (за млн токенов)")
    for row in rows:
        print(
            f"{row['model']:<20} {row['in_per_mtok']:>6.2f} {row['out_per_mtok']:>7.2f}"
            f" {row['cache_write_per_mtok']:>8.2f} {row['cache_write_1h_per_mtok']:>8.2f}"
            f" {row['cache_read_per_mtok']:>8.2f}"
        )
    for row in unknown:
        print(f"без цены: {row['model']} ({_thousands(row['turns'])} ходов, считается нулём)")
    print(f"вся история: ${total:,.2f}".replace(",", " "))
    return 0


def _sessions(limit: int) -> int:
    with connect() as conn:
        rows = recent_sessions(conn, limit)
    if not rows:
        print("сессий нет — выполните `cdash reindex`", file=sys.stderr)
        return 1
    for row in rows:
        prompt = (row["first_prompt"] or "—").replace("\n", " ")[:48]
        last_at = (row["last_at"] or "")[:16].replace("T", " ")
        print(
            f"{row['id'][:8]}  {last_at}  ходов {row['turns']:>5}"
            f"  выход {_thousands(row['tokens_out']):>10}"
            f"  контекст {_thousands(row['last_context']):>9}"
            f"  {row['project'] or '—'}  {prompt}"
        )
    return 0


def _resolve_session_id(conn: object, prefix: str) -> str | None:
    """Разрешить id по префиксу — вводить полный uuid руками неудобно."""
    rows = list(
        conn.execute(  # type: ignore[attr-defined]
            "SELECT id FROM sessions WHERE id LIKE ? ORDER BY last_at DESC", (prefix + "%",)
        )
    )
    if len(rows) == 1:
        return str(rows[0]["id"])
    if not rows:
        return None
    print(f"под «{prefix}» подходит {len(rows)} сессий, уточните:", file=sys.stderr)
    for row in rows[:10]:
        print(f"  {row['id']}", file=sys.stderr)
    return None


def _session(prefix: str) -> int:
    with connect() as conn:
        session_id = _resolve_session_id(conn, prefix)
        if session_id is None:
            print(f"сессия «{prefix}» не найдена", file=sys.stderr)
            return 1
        summary = session_summary(conn, session_id)
        models = session_models(conn, session_id)
        tools = session_tools(conn, session_id)
    if summary is None:
        return 1

    period = f"{(summary.started_at or '')[:19]} → {(summary.last_at or '')[:19]}"
    print(f"сессия       : {summary.session_id}")
    print(f"проект       : {summary.project or '—'} ({summary.root_path or '—'})")
    print(f"период       : {period.replace('T', ' ')}")
    print(f"первый промпт: {(summary.first_prompt or '—')[:70]}")
    sidechain = f" (сабагентов {summary.sidechain_turns})" if summary.sidechain_turns else ""
    print(f"ходов        : {summary.turns}{sidechain}")
    print(f"вход         : {_thousands(summary.input_tokens)}")
    print(f"выход        : {_thousands(summary.output_tokens)}")
    print(f"кэш чтение   : {_thousands(summary.cache_read)}")
    print(
        f"кэш запись   : {_thousands(summary.cache_write)}"
        f" (5m {_thousands(summary.cache_write_5m)}, 1h {_thousands(summary.cache_write_1h)})"
    )
    print(f"стоимость    : ${summary.cost_usd:,.2f}".replace(",", " "))
    print(f"контекст     : {_thousands(summary.last_context)} на последнем ходе")
    if models:
        parts = [f"{model} {turns} ходов / {_thousands(out)}" for model, turns, out in models]
        print(f"модели       : {'; '.join(parts)}")
    if tools:
        print(f"инструменты  : {', '.join(f'{tool} {calls}' for tool, calls in tools)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
