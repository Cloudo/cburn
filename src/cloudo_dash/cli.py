"""CLI `cdash` (TZ §10).

Реализовано: `paths`, `initdb`, `reindex`, `prices`, `sessions`, `session`, `serve`, `otel`.
Фильтры по проекту и периоду и команда `stats` — задача B7.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import __version__, autostart, config, paths, pricing
from .analyzer import advisor, digest
from .collector import otlp
from .collector.indexer import ingest_tree
from .db import connect
from .metrics import (
    idle_turns,
    model_share,
    otel_permissions,
    otel_sessions,
    otel_usage,
    period_start,
    recent_sessions,
    session_chain,
    session_models,
    session_summary,
    session_tools,
    tool_profile,
    window_usage,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cdash", description="Спидометр Claude")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("paths", help="показать пути конфига, БД и транскриптов")
    sub.add_parser("initdb", help="создать БД и применить схему")
    stats = sub.add_parser("stats", help="сводка расхода за период")
    _add_filters(stats)
    sessions = sub.add_parser("sessions", help="список сессий")
    sessions.add_argument("-n", "--limit", type=int, default=20, help="сколько показать")
    # Список и так ограничен -n, поэтому по умолчанию он за всю историю.
    _add_filters(sessions, period="all")
    session = sub.add_parser("session", help="детали одной сессии")
    session.add_argument("session_id", help="полный id или его начало")
    reindex = sub.add_parser("reindex", help="дочитать транскрипты в БД")
    reindex.add_argument(
        "--full", action="store_true", help="перечитать файлы целиком, а не только хвосты"
    )
    reindex.add_argument(
        "--project", help="только транскрипты этого проекта (часть имени или пути)"
    )
    digest_cmd = sub.add_parser("digest", help="выжимка периода для советчика (JSON)")
    _add_filters(digest_cmd, period="24h")
    digest_cmd.add_argument("--out", metavar="ФАЙЛ", help="записать в файл вместо вывода")
    advise = sub.add_parser("advise", help="разобрать период советчиком (claude -p)")
    _add_filters(advise, period="24h")
    advise.add_argument("--model", default=None, help="алиас модели (по умолчанию из конфига)")
    advise.add_argument(
        "--dry-run", action="store_true", help="показать дайджест и команду, не вызывая модель"
    )
    events = sub.add_parser("events", help="незнакомые типы записей транскрипта")
    events.add_argument("--show", metavar="ТИП", help="показать сохранённые примеры записи")
    prices = sub.add_parser("prices", help="применить цены из конфига и пересчитать стоимость")
    prices.add_argument(
        "--init", action="store_true", help="записать в конфиг заготовку тарифов, если их нет"
    )
    install = sub.add_parser("install", help="автозапуск дашборда при логине (launchd)")
    install.add_argument("--port", type=int, help="порт (по умолчанию из конфига)")
    sub.add_parser("uninstall", help="убрать автозапуск")
    sub.add_parser("status", help="что launchd думает про агент автозапуска")
    otel = sub.add_parser("otel", help="телеметрия Claude Code: что дошло и как включить")
    otel.add_argument("--env", action="store_true", help="строки export для профиля шелла")
    otel.add_argument(
        "--settings", action="store_true", help="фрагмент env для ~/.claude/settings.json"
    )
    otel.add_argument("--port", type=int, help="порт дашборда (по умолчанию из конфига)")
    otel.add_argument("--prune", action="store_true", help="убрать данные старше otel.keep_days")
    serve = sub.add_parser("serve", help="запустить API-сервер и дашборд")
    serve.add_argument("--port", type=int, help="порт (по умолчанию из конфига)")
    serve.add_argument("--host", default="127.0.0.1", help="только localhost, TZ §7")
    serve.add_argument("--reload", action="store_true", help="перезапуск при правках кода")
    return parser


def _add_filters(parser: argparse.ArgumentParser, *, period: str = "7d") -> None:
    """Общие фильтры: проект подстрокой пути, период — `today`, `24h`, `7d`, `all`."""
    parser.add_argument("--project", help="часть имени или пути проекта, например cloudo-dash")
    parser.add_argument(
        "--period",
        default=period,
        help=f"today | 24h | 7d | 30d | all | дата (по умолчанию {period})",
    )


def _since(period: str) -> datetime | None:
    """Начало периода; разбор общий с экраном «Сессии»."""
    try:
        return period_start(period)
    except ValueError as exc:
        raise SystemExit(
            f"не разобран период «{period}»: ждём today, 24h, 7d, all или дату"
        ) from exc


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
        return _reindex(args.full, args.project)

    if args.command == "advise":
        return _advise(args.project, args.period, args.model, args.dry_run)

    if args.command == "digest":
        return _digest(args.project, args.period, args.out)

    if args.command == "events":
        return _events(args.show)

    if args.command == "prices":
        return _prices(args.init)

    if args.command == "stats":
        return _stats(args.project, args.period)

    if args.command == "sessions":
        return _sessions(args.limit, args.project, args.period)

    if args.command == "session":
        return _session(args.session_id)

    if args.command == "install":
        print(autostart.install(args.port or config.load()["server"]["port"]))
        return 0

    if args.command == "uninstall":
        print(autostart.uninstall())
        return 0

    if args.command == "status":
        print(autostart.status())
        return 0

    if args.command == "otel":
        return _otel(args.env, args.settings, args.port, args.prune)

    if args.command == "serve":
        return _serve(args.host, args.port, args.reload)

    print(f"неизвестная команда `{args.command}`", file=sys.stderr)
    return 2


def _otel_env(port: int) -> dict[str, str]:
    """Переменные окружения, которыми Claude Code включает телеметрию (веха E).

    Кодировка `http/json`: приёмник разбирает её штатным json и живёт на том же
    порту, что дашборд. Интервалы короче стандартных (60 и 5 секунд), потому
    что телеметрия здесь идёт на живой прибор, а не в хранилище на потом.
    """
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{port}/otlp",
        "OTEL_METRIC_EXPORT_INTERVAL": "10000",
        "OTEL_LOGS_EXPORT_INTERVAL": "5000",
    }


def _otel(show_env: bool, show_settings: bool, port: int | None, prune: bool = False) -> int:
    """Состояние приёма телеметрии и способ её включить.

    Сами переменные окружения приложение не прописывает: `~/.claude` открыт
    только на чтение, а трогать профиль шелла за человека — не его дело.
    """
    bind_port = port or int(config.load()["server"]["port"])
    env = _otel_env(bind_port)
    if show_env:
        for name, value in env.items():
            print(f"export {name}={value}")
        return 0
    if show_settings:
        print(json.dumps({"env": env}, ensure_ascii=False, indent=2))
        return 0

    settings = config.load()["otel"]
    keep_days = int(settings.get("keep_days") or 0)
    with connect() as conn:
        if prune:
            removed = otlp.prune(conn, keep_days)
            print(f"убрано: метрик {removed['metrics']}, событий {removed['events']}")
        state = otlp.status(conn)
    print(
        f"приёмник : http://127.0.0.1:{bind_port}/otlp (в конфиге: "
        f"{'включён' if settings['enabled'] else 'выключен'})"
    )
    if not state["signals"]:
        print("посылок не было — телеметрия в Claude Code ещё не включена")
    for signal, row in state["signals"].items():
        print(
            f"{signal:8}: посылок {row['batches']}, записей {row['stored']}, "
            f"потеряно {row['dropped']}, последняя {row['last_at']}"
        )
    # Посылки идут, а записей нет — почти всегда это чужая кодировка: приёмник
    # читает только `http/json`, а `http/protobuf` разобрать нечем.
    if state["signals"] and not any(row["stored"] for row in state["signals"].values()):
        print(
            "посылки приходят, но не разобраны — проверьте"
            " OTEL_EXPORTER_OTLP_PROTOCOL=http/json (`cdash otel --env`)"
        )
    for row in state["metrics"]:
        print(f"  {row['name']:34} точек {row['points']:6}  сумма {row['total']:,.2f}")
    for row in state["events"]:
        print(f"  событие {row['name']:26} {row['records']:6}")
    if state["signals"]:
        with connect() as conn:
            counts = otel_sessions(conn, datetime.now(UTC) - timedelta(days=1))
        starts = ", ".join(
            f"{row['start_type']} {int(row['sessions'])}" for row in counts["starts"]
        )
        print(
            f"сессии за сутки: {counts['telemetry']} по телеметрии,"
            f" {counts['transcripts']} по транскриптам" + (f" ({starts})" if starts else "")
        )
    stored = state["stored"]
    if stored["rows"]:
        keep = f"хранится {keep_days} суток" if keep_days else "хранится всё"
        print(
            f"накоплено: {_thousands(stored['rows'])} строк, "
            f"{stored['bytes'] / 1024:,.0f} КБ атрибутов, с {stored['oldest']} ({keep})"
        )
    if not state["signals"]:
        print()
        print("включить (в профиль шелла или в env-секцию ~/.claude/settings.json):")
        print("  cdash otel --env        # строки export")
        print("  cdash otel --settings   # фрагмент для settings.json")
        print("после правки Claude Code надо перезапустить — окружение читается на старте")
    return 0


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


def _usd(value: float) -> str:
    """Доллары с разрядами. Подписка ими не оплачивается — это вес расхода."""
    return "$" + f"{value:,.2f}".replace(",", " ")


def _reindex(full: bool = False, project: str | None = None) -> int:
    """Дочитать все транскрипты (задача B2).

    `--full` сбрасывает сохранённые offset и читает файлы целиком: нужно, когда
    поменялась логика разбора — например, чтобы проставить связи resume-форков
    на уже прочитанной истории. Повторные ходы гасятся по `message_id`.
    """
    started = time.monotonic()
    roots = _project_dirs(project)
    if not roots:
        print(f"проект «{project}» не найден в {paths.CLAUDE_PROJECTS_DIR}", file=sys.stderr)
        return 1
    with connect() as conn:
        pricing.sync_prices(conn, config.load())
        if full:
            with conn:  # выводимые из данных вещи собираются заново, иначе задвоятся
                conn.execute("DELETE FROM files")
                conn.execute("DELETE FROM raw_events")
                conn.execute("DELETE FROM raw_event_counts")
                conn.execute("UPDATE sessions SET parent_session_id = NULL")
        results = [stats for root in roots for stats in ingest_tree(conn, root, on_file=_progress)]
    if sys.stderr.isatty():
        print(file=sys.stderr)  # закрыть строку прогресса
    lines = sum(result.lines for result in results)
    turns = sum(result.turns_new for result in results)
    known = sum(result.turns_known for result in results)
    elapsed = time.monotonic() - started
    print(f"файлов: {len(results)}, прочитано строк: {_thousands(lines)}, за {elapsed:.1f} с")
    print(f"новых ходов: {_thousands(turns)}, уже известных: {_thousands(known)}")
    return 0


def _project_dirs(project: str | None) -> list[Path]:
    """Каталоги транскриптов для обхода: весь корень или только нужный проект."""
    root = paths.CLAUDE_PROJECTS_DIR
    if not project:
        return [root]
    return sorted(path for path in root.glob("*") if path.is_dir() and project in path.name)


def _progress(done: int, total: int, path: Path) -> None:
    """Живая строка обхода. В пайп не пишется: там нужен только итог."""
    if not sys.stderr.isatty():
        return
    print(f"\r{done}/{total}  {path.name[:36]:<36}", end="", file=sys.stderr, flush=True)


def _advise(project: str | None, period: str, model: str | None, dry_run: bool) -> int:
    """Прогнать дайджест через советчик (задача D2)."""
    cfg = config.load()
    chosen = model or cfg["analyzer"]["model"]
    since = _since(period) or datetime.fromtimestamp(0, UTC)
    with connect() as conn:
        payload = digest.build(conn, since, config=cfg, project=project)
        if dry_run:
            print(" ".join(advisor.build_command(chosen)))
            print(f"дайджест: ~{payload['size']['tokens_approx']} токенов")
            return 0
        if not payload["usage"]["turns"]:
            print("за период ходов нет — советовать не о чем", file=sys.stderr)
            return 1
        try:
            result = advisor.advise(conn, payload, model=chosen)
        except (RuntimeError, OSError) as exc:
            print(f"советчик не отработал: {exc}", file=sys.stderr)
            return 1

    print(f"модель {result['model']}, такт стоил {_usd(result['cost_usd'])}")
    if not result["advice"]:
        print("советов нет — либо всё ровно, либо совет не подкрепился цифрами")
        return 0
    for item in result["advice"]:
        print(f"\n[{item['severity']}] {item['title']}")
        if item["detail"]:
            print(f"  {item['detail']}")
        if item["action"]:
            print(f"  что сделать: {item['action']}")
        print(f"  на основании: {item['evidence']}")
    return 0


def _digest(project: str | None, period: str, out: str | None) -> int:
    """Собрать дайджест периода — вход советчика (задача D1)."""
    since = _since(period) or datetime.fromtimestamp(0, UTC)
    with connect() as conn:
        payload = digest.build(conn, since, config=config.load(), project=project)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        size = payload["size"]
        print(f"дайджест записан в {out}: ~{size['tokens_approx']} токенов из {size['limit']}")
    else:
        print(text)
    return 0 if payload["size"]["within_limit"] else 1


def _events(show: str | None) -> int:
    """Незнакомые записи: что встречается и как выглядит (задача B6)."""
    with connect() as conn:
        if show:
            rows = conn.execute(
                "SELECT ts, version, payload FROM raw_events WHERE type = ? ORDER BY id",
                (show,),
            ).fetchall()
            if not rows:
                print(f"примеров записи «{show}» не сохранено", file=sys.stderr)
                return 1
            for row in rows:
                print(f"--- {row['ts'] or '—'}  версия {row['version'] or '—'}")
                print(row["payload"][:2000])
            return 0
        counts = conn.execute(
            "SELECT type, version, seen, first_at, last_at FROM raw_event_counts ORDER BY seen DESC"
        ).fetchall()
    if not counts:
        print("незнакомых записей нет — выполните `cdash reindex`", file=sys.stderr)
        return 1
    print("тип                       версия     сколько  впервые")
    for row in counts:
        first = (row["first_at"] or "—")[:16].replace("T", " ")
        print(
            f"{row['type']:<25} {row['version'] or '—':<10} {_thousands(row['seen']):>8}  {first}"
        )
    print("примеры: cdash events --show <тип>")
    return 0


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
    print(f"вся история: {_usd(total)}")
    return 0


def _stats(project: str | None, period: str) -> int:
    """Сводка расхода за период (ТЗ §4, задача B7)."""
    since = _since(period) or datetime.fromtimestamp(0, UTC)
    with connect() as conn:
        usage = window_usage(conn, since, project=project)
        models = model_share(conn, since, project=project)
        profile = tool_profile(conn, since, project=project)
        idle = idle_turns(conn, since, project=project)
        # Служебные запросы Claude Code в транскрипт не попадают: без этой
        # строки сводка молча занижала бы расход (веха E).
        off_transcript = otel_usage(conn, since, project=project)
        permissions = otel_permissions(conn, since, project=project)
    if not usage["turns"]:
        print("за период ходов нет", file=sys.stderr)
        return 1

    where = f", проект ~ {project}" if project else ""
    print(f"период       : {period}{where}")
    print(f"ходов        : {_thousands(usage['turns'])} в {usage['sessions']} сессиях")
    print(f"вход         : {_thousands(usage['input_tokens'])}")
    print(f"выход        : {_thousands(usage['output_tokens'])}")
    print(f"кэш чтение   : {_thousands(usage['cache_read'])}")
    print(
        f"кэш запись   : {_thousands(usage['cache_write'])}"
        f" (5m {_thousands(usage['cache_write_5m'])},"
        f" 1h {_thousands(usage['cache_write_1h'])})"
    )
    print(f"стоимость    : {_usd(usage['cost_usd'])}")
    if models:
        parts = [f"{_model_label(row['model'])} {row['turns']}" for row in models]
        print(f"модели       : {', '.join(parts)}")
    if profile["tools"]:
        parts = [f"{_tool_label(row['tool'])} {row['calls']}" for row in profile["tools"][:6]]
        print(f"инструменты  : {', '.join(parts)} (всего {profile['tools_total']})")
    if profile["bash_commands"]:
        parts = [f"{row['command']} {row['calls']}" for row in profile["bash_commands"][:5]]
        print(f"внутри Bash  : {', '.join(parts)}")
    print(
        f"холостые     : {idle['turns']} ходов ({idle['share'] * 100:.0f}%),"
        f" прочитано из кэша {_thousands(idle['cache_read'])}"
    )
    if off_transcript["tokens"]:
        print(
            f"мимо истории : {_thousands(int(off_transcript['tokens']))} токенов,"
            f" {_usd(off_transcript['cost_usd'])}"
            f" ({off_transcript['share'] * 100:.1f}% расхода) — служебные запросы"
        )
    if permissions["decisions"]:
        print(
            f"разрешения   : {permissions['manual']} подтверждено руками,"
            f" {permissions['auto']} автоматически"
        )
    return 0


def _model_label(model: str) -> str:
    """Короткое имя модели: `claude-` и дата выпуска в выводе только мешают."""
    short = model.removeprefix("claude-")
    head, _, tail = short.rpartition("-")
    return head if head and tail.isdigit() and len(tail) == 8 else short


def _tool_label(tool: str) -> str:
    """`mcp__plugin_playwright_playwright__browser_click` → `playwright: browser_click`."""
    if not tool.startswith("mcp__"):
        return tool
    parts = tool.removeprefix("mcp__").split("__")
    name = parts.pop()
    words = dict.fromkeys((parts.pop() if parts else "").split("_"))
    server = list(words)[-1] if words else ""
    return f"{server}: {name}" if server else name


def _sessions(limit: int, project: str | None = None, period: str = "all") -> int:
    with connect() as conn:
        rows = recent_sessions(conn, limit, project=project, since=_since(period))
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
        chain = session_chain(conn, session_id)
    if summary is None:
        return 1

    period = f"{(summary.started_at or '')[:19]} → {(summary.last_at or '')[:19]}"
    print(f"сессия       : {summary.session_id}")
    print(f"проект       : {summary.project or '—'} ({summary.root_path or '—'})")
    print(f"период       : {period.replace('T', ' ')}")
    print(f"первый промпт: {(summary.first_prompt or '—')[:70]}")
    print(f"ходов        : {summary.turns}")
    if summary.sidechain_turns:
        print(
            f"сабагенты    : ходов {summary.sidechain_turns},"
            f" токенов {_thousands(summary.sidechain_tokens)},"
            f" {_usd(summary.sidechain_cost_usd)}"
        )
    print(f"вход         : {_thousands(summary.input_tokens)}")
    print(f"выход        : {_thousands(summary.output_tokens)}")
    print(f"кэш чтение   : {_thousands(summary.cache_read)}")
    print(
        f"кэш запись   : {_thousands(summary.cache_write)}"
        f" (5m {_thousands(summary.cache_write_5m)}, 1h {_thousands(summary.cache_write_1h)})"
    )
    print(f"стоимость    : {_usd(summary.cost_usd)}")
    print(f"контекст     : {_thousands(summary.last_context)} на последнем ходе")
    if summary.parent_session_id:
        print(f"продолжает   : {summary.parent_session_id[:8]}")
    if len(chain["sessions"]) > 1:
        print(
            f"линия работы : сессий {len(chain['sessions'])},"
            f" ходов {_thousands(chain['turns'])},"
            f" {_usd(chain['cost_usd'])}"
        )
    if models:
        parts = [
            f"{_model_label(model)} {turns} ходов / {_thousands(out)}"
            for model, turns, out in models
        ]
        print(f"модели       : {'; '.join(parts)}")
    if tools:
        parts = [f"{_tool_label(tool)} {calls}" for tool, calls in tools]
        print(f"инструменты  : {', '.join(parts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
