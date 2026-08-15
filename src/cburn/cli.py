"""CLI `cburn` (TZ §10).

Implemented: `paths`, `initdb`, `reindex`, `prices`, `sessions`, `session`, `serve`, `otel`.
Project and period filters and the `stats` command are task B7.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import __version__, autostart, config, instance, paths, pricing
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
    parser = argparse.ArgumentParser(prog="cburn", description="The Claude speedometer")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("paths", help="show the config, database and transcript paths")
    sub.add_parser("initdb", help="create the database and apply the schema")
    stats = sub.add_parser("stats", help="the spend summary for a period")
    _add_filters(stats)
    sessions = sub.add_parser("sessions", help="the session list")
    sessions.add_argument("-n", "--limit", type=int, default=20, help="how many to show")
    # The list is already bounded by -n, so by default it covers the whole history.
    _add_filters(sessions, period="all")
    session = sub.add_parser("session", help="the details of one session")
    session.add_argument("session_id", help="the full id or its beginning")
    reindex = sub.add_parser("reindex", help="read the transcripts into the database")
    reindex.add_argument(
        "--full", action="store_true", help="re-read the files whole, not only the tails"
    )
    reindex.add_argument("--project", help="only this project's transcripts (name or path part)")
    digest_cmd = sub.add_parser("digest", help="the period digest for the advisor (JSON)")
    _add_filters(digest_cmd, period="24h")
    digest_cmd.add_argument("--out", metavar="FILE", help="write into a file instead of stdout")
    advise = sub.add_parser("advise", help="analyse the period with the advisor (claude -p)")
    _add_filters(advise, period="24h")
    advise.add_argument("--model", default=None, help="model alias (from the config by default)")
    advise.add_argument(
        "--dry-run", action="store_true", help="show the digest and the command, skip the model"
    )
    events = sub.add_parser("events", help="unknown transcript record types")
    events.add_argument("--show", metavar="TYPE", help="show the stored samples of a record")
    prices = sub.add_parser("prices", help="apply the config prices and recompute the cost")
    prices.add_argument(
        "--init", action="store_true", help="write a rate template into the config if absent"
    )
    install = sub.add_parser("install", help="autostart the dashboard at login (launchd)")
    install.add_argument("--port", type=int, help="port (from the config by default)")
    sub.add_parser("uninstall", help="remove the autostart")
    sub.add_parser("status", help="what launchd thinks about the autostart agent")
    otel = sub.add_parser("otel", help="Claude Code telemetry: what arrived and how to enable")
    otel.add_argument("--env", action="store_true", help="export lines for the shell profile")
    otel.add_argument(
        "--settings", action="store_true", help="an env snippet for ~/.claude/settings.json"
    )
    otel.add_argument("--port", type=int, help="dashboard port (from the config by default)")
    otel.add_argument("--prune", action="store_true", help="remove data older than otel.keep_days")
    serve = sub.add_parser("serve", help="start the API server and the dashboard")
    serve.add_argument("--port", type=int, help="port (from the config by default)")
    serve.add_argument("--host", default="127.0.0.1", help="localhost only, TZ §7")
    serve.add_argument("--reload", action="store_true", help="restart on code edits")
    return parser


def _add_filters(parser: argparse.ArgumentParser, *, period: str = "7d") -> None:
    """Shared filters: project as a path substring, period as `today`, `24h`, `7d`, `all`."""
    parser.add_argument("--project", help="part of the project name or path, for example cburn")
    parser.add_argument(
        "--period",
        default=period,
        help=f"today | 24h | 7d | 30d | all | date (default {period})",
    )


def _since(period: str) -> datetime | None:
    """The start of a period; the parsing is shared with the "Sessions" screen."""
    try:
        return period_start(period)
    except ValueError as exc:
        raise SystemExit(
            f"period {period!r} not parsed: expected today, 24h, 7d, all or a date"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "paths":
        cfg = config.load()
        print(f"transcripts : {paths.CLAUDE_PROJECTS_DIR} (read-only)")
        print(f"config      : {paths.CONFIG_PATH}")
        print(f"database    : {paths.DB_PATH}")
        print(f"port        : {cfg['server']['port']}")
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

    print(f"unknown command `{args.command}`", file=sys.stderr)
    return 2


def _otel_env(port: int) -> dict[str, str]:
    """The environment variables Claude Code switches telemetry on with (milestone E).

    The `http/json` encoding: the receiver parses it with the standard json module and
    lives on the same port as the dashboard. The intervals are shorter than the standard
    ones (60 and 5 seconds), because here telemetry feeds a live instrument, not storage.
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
    """The state of telemetry reception and the way to switch it on.

    The application does not write the environment variables itself: `~/.claude` is open
    read-only, and touching the shell profile on a human's behalf is not its business.
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
            print(f"removed: metrics {removed['metrics']}, events {removed['events']}")
        state = otlp.status(conn)
    print(
        f"receiver : http://127.0.0.1:{bind_port}/otlp (in the config: "
        f"{'on' if settings['enabled'] else 'off'})"
    )
    if not state["signals"]:
        print("no payloads arrived - telemetry is not switched on in Claude Code yet")
    for signal, row in state["signals"].items():
        print(
            f"{signal:8}: payloads {row['batches']}, records {row['stored']}, "
            f"lost {row['dropped']}, last {row['last_at']}"
        )
    # Payloads arrive but no records appear - almost always that is a foreign encoding:
    # the receiver reads only `http/json`, and `http/protobuf` cannot be parsed here.
    if state["signals"] and not any(row["stored"] for row in state["signals"].values()):
        print(
            "payloads arrive but are not parsed - check"
            " OTEL_EXPORTER_OTLP_PROTOCOL=http/json (`cburn otel --env`)"
        )
    for row in state["metrics"]:
        print(f"  {row['name']:34} points {row['points']:6}  total {row['total']:,.2f}")
    for row in state["events"]:
        print(f"  event {row['name']:28} {row['records']:6}")
    if state["signals"]:
        with connect() as conn:
            counts = otel_sessions(conn, datetime.now(UTC) - timedelta(days=1))
        starts = ", ".join(
            f"{row['start_type']} {int(row['sessions'])}" for row in counts["starts"]
        )
        print(
            f"sessions over a day: {counts['telemetry']} by telemetry,"
            f" {counts['transcripts']} by transcripts" + (f" ({starts})" if starts else "")
        )
    stored = state["stored"]
    if stored["rows"]:
        keep = f"kept for {keep_days} days" if keep_days else "kept forever"
        print(
            f"accumulated: {_thousands(stored['rows'])} rows, "
            f"{stored['bytes'] / 1024:,.0f} KB of attributes, since {stored['oldest']} ({keep})"
        )
    if not state["signals"]:
        print()
        print("to switch on (in the shell profile or the env section of ~/.claude/settings.json):")
        print("  cburn otel --env        # export lines")
        print("  cburn otel --settings   # a snippet for settings.json")
        print("after the edit Claude Code has to be restarted - it reads the environment at start")
    return 0


def _serve(host: str, port: int | None, reload: bool) -> int:
    """Bring up the API together with the watcher in one process.

    A single copy: a second server would read the transcripts a second time and send
    the notifications a second time, whatever port it is given (`instance.only_one`).
    """
    import uvicorn

    from .api.server import create_app

    cfg = config.load()
    bind_port = port or int(cfg["server"]["port"])
    try:
        with instance.only_one():
            print(f"cburn: http://{host}:{bind_port}  (Ctrl+C to stop)")
            uvicorn.run(
                "cburn.api.server:create_app" if reload else create_app(),
                host=host,
                port=bind_port,
                factory=reload,
                reload=reload,
                log_level="warning",
            )
    except instance.AlreadyRunning as exc:
        print(
            f"{exc} - one watcher per machine, a second one would count everything twice",
            file=sys.stderr,
        )
        return 1
    return 0


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _usd(value: float) -> str:
    """Dollars with separators. A subscription is not paid in them - it is the spend weight."""
    return "$" + f"{value:,.2f}".replace(",", " ")


def _reindex(full: bool = False, project: str | None = None) -> int:
    """Read all the transcripts (task B2).

    `--full` resets the stored offsets and reads the files whole: needed when the parsing
    logic has changed - for instance, to fill in resume-fork links over already read
    history. Repeated turns are swallowed by `message_id`.
    """
    started = time.monotonic()
    roots = _project_dirs(project)
    if not roots:
        print(f"project {project!r} not found in {paths.CLAUDE_PROJECTS_DIR}", file=sys.stderr)
        return 1
    with connect() as conn:
        pricing.sync_prices(conn, config.load())
        if full:
            with conn:  # things derived from the data are rebuilt, otherwise they double
                conn.execute("DELETE FROM files")
                conn.execute("DELETE FROM raw_events")
                conn.execute("DELETE FROM raw_event_counts")
                conn.execute("UPDATE sessions SET parent_session_id = NULL")
        results = [stats for root in roots for stats in ingest_tree(conn, root, on_file=_progress)]
    if sys.stderr.isatty():
        print(file=sys.stderr)  # close the progress line
    lines = sum(result.lines for result in results)
    turns = sum(result.turns_new for result in results)
    known = sum(result.turns_known for result in results)
    elapsed = time.monotonic() - started
    print(f"files: {len(results)}, lines read: {_thousands(lines)}, in {elapsed:.1f} s")
    print(f"new turns: {_thousands(turns)}, already known: {_thousands(known)}")
    return 0


def _project_dirs(project: str | None) -> list[Path]:
    """Transcript directories to walk: the whole root or only the project asked for."""
    root = paths.CLAUDE_PROJECTS_DIR
    if not project:
        return [root]
    return sorted(path for path in root.glob("*") if path.is_dir() and project in path.name)


def _progress(done: int, total: int, path: Path) -> None:
    """A live walk line. It is not written into a pipe: there only the result matters."""
    if not sys.stderr.isatty():
        return
    print(f"\r{done}/{total}  {path.name[:36]:<36}", end="", file=sys.stderr, flush=True)


def _advise(project: str | None, period: str, model: str | None, dry_run: bool) -> int:
    """Run the digest through the advisor (task D2)."""
    cfg = config.load()
    chosen = model or cfg["analyzer"]["model"]
    language = str(cfg["analyzer"].get("language") or "en")
    since = _since(period) or datetime.fromtimestamp(0, UTC)
    with connect() as conn:
        payload = digest.build(conn, since, config=cfg, project=project)
        if dry_run:
            print(" ".join(advisor.build_command(chosen, language=language)))
            print(f"digest: ~{payload['size']['tokens_approx']} tokens")
            return 0
        if not payload["usage"]["turns"]:
            print("no turns in the period - nothing to advise about", file=sys.stderr)
            return 1
        try:
            result = advisor.advise(conn, payload, model=chosen, language=language)
        except (RuntimeError, OSError) as exc:
            print(f"the advisor failed: {exc}", file=sys.stderr)
            return 1

    print(f"model {result['model']}, the tick cost {_usd(result['cost_usd'])}")
    if not result["advice"]:
        print("no tips - either all is well or no tip was backed by numbers")
        return 0
    for item in result["advice"]:
        print(f"\n[{item['severity']}] {item['title']}")
        if item["detail"]:
            print(f"  {item['detail']}")
        if item["action"]:
            print(f"  what to do: {item['action']}")
        print(f"  based on: {item['evidence']}")
    return 0


def _digest(project: str | None, period: str, out: str | None) -> int:
    """Build the period digest - the advisor's input (task D1)."""
    since = _since(period) or datetime.fromtimestamp(0, UTC)
    with connect() as conn:
        payload = digest.build(conn, since, config=config.load(), project=project)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        size = payload["size"]
        print(f"digest written to {out}: ~{size['tokens_approx']} tokens out of {size['limit']}")
    else:
        print(text)
    return 0 if payload["size"]["within_limit"] else 1


def _events(show: str | None) -> int:
    """Unknown records: what shows up and what it looks like (task B6)."""
    with connect() as conn:
        if show:
            rows = conn.execute(
                "SELECT ts, version, payload FROM raw_events WHERE type = ? ORDER BY id",
                (show,),
            ).fetchall()
            if not rows:
                print(f"no samples of record {show!r} are stored", file=sys.stderr)
                return 1
            for row in rows:
                print(f"--- {row['ts'] or '-'}  version {row['version'] or '-'}")
                print(row["payload"][:2000])
            return 0
        counts = conn.execute(
            "SELECT type, version, seen, first_at, last_at FROM raw_event_counts ORDER BY seen DESC"
        ).fetchall()
    if not counts:
        print("no unknown records - run `cburn reindex`", file=sys.stderr)
        return 1
    print("type                      version      count  first seen")
    for row in counts:
        first = (row["first_at"] or "-")[:16].replace("T", " ")
        print(
            f"{row['type']:<25} {row['version'] or '-':<10} {_thousands(row['seen']):>8}  {first}"
        )
    print("samples: cburn events --show <type>")
    return 0


def _prices(init: bool) -> int:
    """Apply `[prices]` from the config to the whole history."""
    cfg = config.load()
    if init and not cfg["prices"]:
        cfg["prices"] = pricing.sample_prices()
        config.save(cfg)
        print(f"a rate template was written to {paths.CONFIG_PATH} - check the prices")
    with connect() as conn:
        models = pricing.recalculate(conn, cfg)
        rows = pricing.known_prices(conn)
        unknown = pricing.unknown_models(conn)
        total = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM turns").fetchone()[0]
    if not models:
        print("no prices: fill the [prices] config section or run `cburn prices --init`")
        return 1
    print("model                  input  output  cache 5m  cache 1h    read  (per Mtok)")
    for row in rows:
        print(
            f"{row['model']:<20} {row['in_per_mtok']:>6.2f} {row['out_per_mtok']:>7.2f}"
            f" {row['cache_write_per_mtok']:>8.2f} {row['cache_write_1h_per_mtok']:>8.2f}"
            f" {row['cache_read_per_mtok']:>8.2f}"
        )
    for row in unknown:
        print(f"no price: {row['model']} ({_thousands(row['turns'])} turns, counted as zero)")
    print(f"whole history: {_usd(total)}")
    return 0


def _stats(project: str | None, period: str) -> int:
    """The spend summary for a period (TZ §4, task B7)."""
    since = _since(period) or datetime.fromtimestamp(0, UTC)
    with connect() as conn:
        usage = window_usage(conn, since, project=project)
        models = model_share(conn, since, project=project)
        profile = tool_profile(conn, since, project=project)
        idle = idle_turns(conn, since, project=project)
        # Claude Code service requests never reach the transcript: without this line
        # the summary would quietly understate the spend (milestone E).
        off_transcript = otel_usage(conn, since, project=project)
        permissions = otel_permissions(conn, since, project=project)
    if not usage["turns"]:
        print("no turns in the period", file=sys.stderr)
        return 1

    where = f", project ~ {project}" if project else ""
    print(f"period       : {period}{where}")
    print(f"turns        : {_thousands(usage['turns'])} in {usage['sessions']} sessions")
    print(f"input        : {_thousands(usage['input_tokens'])}")
    print(f"output       : {_thousands(usage['output_tokens'])}")
    print(f"cache read   : {_thousands(usage['cache_read'])}")
    print(
        f"cache write  : {_thousands(usage['cache_write'])}"
        f" (5m {_thousands(usage['cache_write_5m'])},"
        f" 1h {_thousands(usage['cache_write_1h'])})"
    )
    print(f"cost         : {_usd(usage['cost_usd'])}")
    if models:
        parts = [f"{_model_label(row['model'])} {row['turns']}" for row in models]
        print(f"models       : {', '.join(parts)}")
    if profile["tools"]:
        parts = [f"{_tool_label(row['tool'])} {row['calls']}" for row in profile["tools"][:6]]
        print(f"tools        : {', '.join(parts)} (total {profile['tools_total']})")
    if profile["bash_commands"]:
        parts = [f"{row['command']} {row['calls']}" for row in profile["bash_commands"][:5]]
        print(f"inside Bash  : {', '.join(parts)}")
    print(
        f"idle         : {idle['turns']} turns ({idle['share'] * 100:.0f}%),"
        f" read from cache {_thousands(idle['cache_read'])}"
    )
    if off_transcript["tokens"]:
        print(
            f"off history  : {_thousands(int(off_transcript['tokens']))} tokens,"
            f" {_usd(off_transcript['cost_usd'])}"
            f" ({off_transcript['share'] * 100:.1f}% of spend) - service requests"
        )
    if permissions["decisions"]:
        print(
            f"permissions  : {permissions['manual']} confirmed by hand,"
            f" {permissions['auto']} automatically"
        )
    return 0


def _model_label(model: str) -> str:
    """A short model name: `claude-` and the release date only get in the way here."""
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
        print("no sessions - run `cburn reindex`", file=sys.stderr)
        return 1
    for row in rows:
        prompt = (row["first_prompt"] or "-").replace("\n", " ")[:48]
        last_at = (row["last_at"] or "")[:16].replace("T", " ")
        print(
            f"{row['id'][:8]}  {last_at}  turns {row['turns']:>5}"
            f"  output {_thousands(row['tokens_out']):>10}"
            f"  context {_thousands(row['last_context']):>9}"
            f"  {row['project'] or '-'}  {prompt}"
        )
    return 0


def _resolve_session_id(conn: object, prefix: str) -> str | None:
    """Resolve an id by prefix - typing a full uuid by hand is inconvenient."""
    rows = list(
        conn.execute(  # type: ignore[attr-defined]
            "SELECT id FROM sessions WHERE id LIKE ? ORDER BY last_at DESC", (prefix + "%",)
        )
    )
    if len(rows) == 1:
        return str(rows[0]["id"])
    if not rows:
        return None
    print(f"{prefix!r} matches {len(rows)} sessions, be more specific:", file=sys.stderr)
    for row in rows[:10]:
        print(f"  {row['id']}", file=sys.stderr)
    return None


def _session(prefix: str) -> int:
    with connect() as conn:
        session_id = _resolve_session_id(conn, prefix)
        if session_id is None:
            print(f"session {prefix!r} not found", file=sys.stderr)
            return 1
        summary = session_summary(conn, session_id)
        models = session_models(conn, session_id)
        tools = session_tools(conn, session_id)
        chain = session_chain(conn, session_id)
    if summary is None:
        return 1

    period = f"{(summary.started_at or '')[:19]} - {(summary.last_at or '')[:19]}"
    print(f"session      : {summary.session_id}")
    print(f"project      : {summary.project or '-'} ({summary.root_path or '-'})")
    print(f"period       : {period.replace('T', ' ')}")
    print(f"first prompt : {(summary.first_prompt or '-')[:70]}")
    print(f"turns        : {summary.turns}")
    if summary.sidechain_turns:
        print(
            f"subagents    : turns {summary.sidechain_turns},"
            f" tokens {_thousands(summary.sidechain_tokens)},"
            f" {_usd(summary.sidechain_cost_usd)}"
        )
    print(f"input        : {_thousands(summary.input_tokens)}")
    print(f"output       : {_thousands(summary.output_tokens)}")
    print(f"cache read   : {_thousands(summary.cache_read)}")
    print(
        f"cache write  : {_thousands(summary.cache_write)}"
        f" (5m {_thousands(summary.cache_write_5m)}, 1h {_thousands(summary.cache_write_1h)})"
    )
    print(f"cost         : {_usd(summary.cost_usd)}")
    print(f"context      : {_thousands(summary.last_context)} on the last turn")
    if summary.parent_session_id:
        print(f"continues    : {summary.parent_session_id[:8]}")
    if len(chain["sessions"]) > 1:
        print(
            f"work line    : sessions {len(chain['sessions'])},"
            f" turns {_thousands(chain['turns'])},"
            f" {_usd(chain['cost_usd'])}"
        )
    if models:
        parts = [
            f"{_model_label(model)} {turns} turns / {_thousands(out)}"
            for model, turns, out in models
        ]
        print(f"models       : {'; '.join(parts)}")
    if tools:
        parts = [f"{_tool_label(tool)} {calls}" for tool, calls in tools]
        print(f"tools        : {', '.join(parts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
