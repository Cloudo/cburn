"""HTTP and WebSocket on 127.0.0.1 (task A5, SPEC §5).

The server does not face outwards: it listens on localhost only. The frontend talks to
the backend exclusively through these endpoints - it has no direct filesystem access,
otherwise the Tauri wrapper in M5 would have demanded a rewrite.

The watcher lives in the same process: in its own thread it reads transcript tails and
through `on_ingest` pushes an event into an asyncio queue, from where it goes to every
subscriber of `/ws`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse, Response
from starlette.types import Scope

from .. import actions, config, notifier, paths, pricing, ui_state
from ..analyzer import scheduler
from ..collector import otlp
from ..collector.indexer import IngestStats
from ..collector.watcher import TranscriptWatcher
from ..db import connect
from ..limits import LimitsWatcher
from ..metrics import (
    advice_history,
    known_projects,
    local_day_start,
    otel_state,
    overview,
    period_start,
    refresh_liveness,
    session_chain,
    session_events,
    session_models,
    session_prompts,
    session_summary,
    session_tool_times,
    session_tools,
    session_turns,
    sessions_page,
    set_advice_status,
    set_hidden,
)
from ..processes import live_state, process_for_session, terminate

log = logging.getLogger(__name__)

#: How often the overview goes to subscribers even when the transcripts are quiet.
#: Without this the needle would freeze at the last turn: burn rate windows slide, and
#: in a pause the spend must fall by itself. Recomputing the overview costs about 2 ms,
#: so a one-second tick loads nothing.
TICK_SECONDS = 1.0

#: How often to reconcile sessions with Claude Code processes. More often than sessions
#: appear and end, because the same pass refreshes busyness - and that changes with
#: every command. The expensive `claude agents --json` is cached inside `processes`
#: meanwhile, only `ps` goes out every time.
LIVENESS_SECONDS = 5.0

#: How long a ready telemetry slice lives. The exporter sends payloads every
#: 5-10 seconds, there is nothing to recompute more often, and it costs an order of
#: magnitude more than the rest of the overview (`tools/otel_bench.py`).
OTEL_CACHE_SECONDS = 5.0

#: The liveness poll: live sessions and the start moment of their youngest child.
#: None means asking failed (see `processes.live_state`).
LivenessProbe = Callable[[], dict[str, datetime | None] | None]

#: The built frontend (task A6). Until it exists, a stub is served.
#:
#: An installed copy carries the frontend inside the package: a wheel holds only what lies
#: under `src/cburn`, and `web/dist` beside the sources would be left behind at the build -
#: the dashboard would answer with a list of endpoints instead of a page. A checkout has no
#: `web_dist` and keeps reading `web/dist`, which is where `make web` puts it.
_PACKAGED_WEB = Path(__file__).resolve().parents[1] / "web_dist"
WEB_DIST = (
    _PACKAGED_WEB
    if (_PACKAGED_WEB / "index.html").exists()
    else Path(__file__).resolve().parents[3] / "web" / "dist"
)


class Hub:
    """WebSocket subscribers and the broadcast to them."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def join(self, socket: WebSocket) -> None:
        await socket.accept()
        async with self._lock:
            self._clients.add(socket)

    async def leave(self, socket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(socket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        for socket in clients:
            try:
                await socket.send_json(message)
            except Exception:  # a dropped client must not break the broadcast
                await self.leave(socket)

    @property
    def size(self) -> int:
        return len(self._clients)


def create_app(
    *,
    db_path: Path | None = None,
    projects_dir: Path | None = None,
    watch: bool = True,
    limits: LimitsWatcher | None = None,
    liveness: LivenessProbe | None = None,
    advisor_run: Any = None,
) -> FastAPI:
    """Assemble the application.

    `watch=False` is for tests where the watcher is not needed; `limits`, `liveness` and
    `advisor_run` are swapped there too, so that tests touch neither the keychain, nor
    the network, nor `claude agents --json`, nor `claude -p`.
    """
    hub = Hub()
    events: asyncio.Queue[IngestStats] = asyncio.Queue()
    # Limits live apart from the database: they come from Anthropic, not from transcripts.
    plan_limits = limits or LimitsWatcher()

    def open_db() -> Any:
        return connect(db_path, apply_schema=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with connect(db_path) as conn:  # the schema must exist before the first request
            pricing.recalculate(conn, config.load())
        loop = asyncio.get_running_loop()
        watcher: TranscriptWatcher | None = None
        pump: asyncio.Task[None] | None = None
        if watch:
            root = projects_dir or paths.CLAUDE_PROJECTS_DIR

            def publish(stats: IngestStats) -> None:
                # Called in the watcher thread, while the queue belongs to the asyncio
                # loop - hence call_soon_threadsafe.
                loop.call_soon_threadsafe(events.put_nowait, stats)

            watcher = TranscriptWatcher(root, db_path=db_path, on_ingest=publish)
            watcher.start()
            pump = asyncio.create_task(_pump(events, hub, collect_overview))
        # The ticker does not depend on the watcher: burn rate windows slide without new turns.
        ticker = asyncio.create_task(_ticker(hub, collect_overview))
        # The advisor costs money on every tick, so it is switched on by config only
        # and skips ticks without activity (task D3).
        advice_loop = asyncio.create_task(scheduler.loop(open_db, config.load, runner=advisor_run))
        # Telemetry piles up faster than the useful data - a cleanup at start and once
        # a day (milestone E). Parser data is left alone in the process.
        housekeeping = asyncio.create_task(_prune_otel(open_db))
        ask_live = liveness or _default_liveness
        # The first pass happens before the first request: otherwise a live session
        # would manage to blink "finished".
        await _refresh_liveness(open_db, ask_live)
        probe = asyncio.create_task(_liveness(open_db, ask_live))
        try:
            yield
        finally:
            if watcher is not None:
                watcher.stop()
            for task in (pump, ticker, probe, advice_loop, housekeeping):
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    app = FastAPI(title="cburn", lifespan=lifespan)

    #: A ready telemetry slice and the time it was computed: the overview goes to
    #: subscribers every second, while a day of telemetry is counted over tens of
    #: thousands of events and changes no more often than the exporter sends.
    otel_cache: dict[str, Any] = {"at": 0.0, "value": None}

    def collect_otel(conn: Any, moment: datetime) -> dict[str, Any]:
        age = time.monotonic() - otel_cache["at"]
        if otel_cache["value"] is None or age >= OTEL_CACHE_SECONDS:
            otel_cache["value"] = otel_state(conn, local_day_start(moment))
            otel_cache["at"] = time.monotonic()
        return dict(otel_cache["value"])

    async def collect_overview() -> dict[str, Any]:
        conn = open_db()
        try:
            payload = overview(conn, otel=collect_otel(conn, datetime.now(UTC)))
        finally:
            conn.close()
        # The request to Anthropic is blocking and happens every five minutes - into a thread.
        payload["plan"] = await asyncio.to_thread(plan_limits.current)
        return payload

    @app.get("/api/overview")
    async def api_overview() -> dict[str, Any]:
        return await collect_overview()

    @app.get("/api/notify")
    async def api_notify_state() -> dict[str, Any]:
        """Notification state: whether a pause is on and what went out last (D5)."""
        conn = open_db()
        try:
            until = notifier.paused_until(conn)
            recent = [
                dict(row)
                for row in conn.execute(
                    "SELECT ts, kind, severity, channel, ok FROM notifications"
                    " ORDER BY ts DESC LIMIT 10"
                )
            ]
            return {
                "mode": (config.load().get("telegram") or {}).get("mode"),
                "paused_until": until.isoformat() if until else None,
                "recent": recent,
            }
        finally:
            conn.close()

    @app.post("/api/notify/pause")
    async def api_notify_pause(on: bool = True) -> dict[str, Any]:
        """Two hours of silence or lifting the pause (the tray item and the UI button).

        `crit` passes through the pause anyway - that is decided by `notifier.dispatch`.
        """
        conn = connect(db_path, apply_schema=False)
        try:
            until = notifier.pause_until() if on else None
            notifier.set_pause(conn, until)
        finally:
            conn.close()
        return {"paused_until": until.isoformat() if until else None}

    @app.get("/api/advice")
    async def api_advice(limit: int = 20) -> dict[str, Any]:
        """Analysis history with nested tips (the "Advice" screen, task D6)."""
        conn = open_db()
        try:
            return {"runs": advice_history(conn, limit)}
        finally:
            conn.close()

    @app.post("/api/advice/items/{item_id}")
    async def api_advice_status(item_id: int, status: str) -> dict[str, Any]:
        """Accept or dismiss a tip. A dismissed one will not come again."""
        conn = connect(db_path, apply_schema=False)
        try:
            try:
                changed = set_advice_status(conn, item_id, status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not changed:
                raise HTTPException(status_code=404, detail="tip not found")
            return {"item_id": item_id, "status": status}
        finally:
            conn.close()

    @app.post("/api/advice/items/{item_id}/plan")
    async def api_act_plan(item_id: int) -> dict[str, Any]:
        """What the tip's action would change: the diff and the hash of the file (D7).

        Nothing is written here. The hash comes back so that the confirmation can be
        checked against exactly the state the human was shown.
        """
        conn = open_db()
        try:
            return _act_plan(conn, item_id).public()
        finally:
            conn.close()

    @app.post("/api/advice/items/{item_id}/apply")
    async def api_act_apply(item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Carry the action out after the confirmation. `hash` is what the preview showed."""
        if not actions.enabled(config.load()):
            raise HTTPException(status_code=403, detail="disabled")
        before_hash = str(payload.get("hash") or "")

        def carry_out() -> dict[str, Any]:
            # The whole thing runs in one thread with a connection of its own: writing a
            # file and terminating a process must not hold the event loop.
            conn = connect(db_path, apply_schema=False)
            try:
                result = actions.apply(
                    conn, _act_of(conn, item_id), before_hash=before_hash, item_id=item_id
                )
                # A carried-out tip is an accepted one: left "new" it could come round
                # again on the next tick.
                set_advice_status(conn, item_id, "accepted")
                return result
            finally:
                conn.close()

        try:
            return await asyncio.to_thread(carry_out)
        except actions.ActError as exc:
            raise HTTPException(status_code=_act_code(exc), detail=exc.reason) from exc

    @app.get("/api/patches")
    async def api_patches(limit: int = 50) -> dict[str, Any]:
        """What has already been carried out, with a way back (task D7)."""
        conn = open_db()
        try:
            return {"patches": actions.history(conn, limit)}
        finally:
            conn.close()

    @app.post("/api/patches/{patch_id}/rollback")
    async def api_patch_rollback(patch_id: int) -> dict[str, Any]:
        """Put the file back - unless it has been changed since we wrote it."""

        def undo() -> dict[str, Any]:
            conn = connect(db_path, apply_schema=False)
            try:
                return actions.rollback(conn, patch_id)
            finally:
                conn.close()

        try:
            return await asyncio.to_thread(undo)
        except actions.ActError as exc:
            raise HTTPException(status_code=_act_code(exc), detail=exc.reason) from exc

    def _act_of(conn: Any, item_id: int) -> dict[str, Any]:
        row = conn.execute("SELECT act_json FROM advice_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="not_found")
        act = actions.normalise(json.loads(row["act_json"]) if row["act_json"] else None)
        if act is None:
            raise HTTPException(status_code=400, detail="unknown_act")
        return act

    def _act_plan(conn: Any, item_id: int) -> actions.Plan:
        if not actions.enabled(config.load()):
            raise HTTPException(status_code=403, detail="disabled")
        try:
            return actions.plan(conn, _act_of(conn, item_id))
        except actions.ActError as exc:
            raise HTTPException(status_code=_act_code(exc), detail=exc.reason) from exc

    @app.post("/api/advice/run")
    async def api_advice_run(period: str = "24h") -> dict[str, Any]:
        """Analyse the period now, without waiting for a tick.

        It costs money, hence a separate button with a confirmation.
        """
        settings = config.load()

        def tick() -> dict[str, Any]:
            conn = connect(db_path, apply_schema=False)
            try:
                since = period_start(period) or datetime.now(UTC) - timedelta(days=1)
                return scheduler.run_tick(
                    conn,
                    settings,
                    scheduler.Tick(scheduler.MANUAL, since, settings["analyzer"]["model"]),
                    runner=advisor_run,
                )
            finally:
                conn.close()

        try:
            return await asyncio.to_thread(tick)
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/config")
    async def api_config() -> dict[str, Any]:
        """Settings exactly as they sit in the file (the "Settings" screen, task C3)."""
        return {"config": config.load(), "path": str(paths.CONFIG_PATH)}

    @app.put("/api/config")
    async def api_config_save(payload: dict[str, Any]) -> dict[str, Any]:
        """Write the settings. Prices are applied at once: recomputing takes seconds."""
        incoming = payload.get("config")
        if not isinstance(incoming, dict):
            raise HTTPException(status_code=400, detail="expected a config object")
        errors = config.validate(incoming)
        if errors:
            raise HTTPException(status_code=400, detail="; ".join(errors))
        config.save(incoming)

        def apply_prices() -> None:
            # The connection is opened right here: an SQLite object belongs to the thread
            # it was created in, and the recomputation goes into a separate one.
            conn = connect(db_path, apply_schema=False)
            try:
                pricing.recalculate(conn, incoming)
            finally:
                conn.close()

        await asyncio.to_thread(apply_prices)
        return {"config": config.load()}

    @app.get("/api/ui")
    async def api_ui() -> dict[str, Any]:
        """The interface language the browser chose, for the native surfaces (the tray)."""
        return ui_state.load()

    @app.post("/api/ui/lang")
    async def api_ui_lang(lang: str) -> dict[str, Any]:
        """Mirror the chosen language so that the tray follows the dashboard.

        The server keeps no language of its own: it only passes the choice on to the
        native part, which has no access to `localStorage`.
        """
        if lang not in ui_state.LANGUAGES:
            raise HTTPException(
                status_code=400,
                detail=f"lang: expected one of {', '.join(sorted(ui_state.LANGUAGES))}",
            )
        await asyncio.to_thread(ui_state.save_lang, lang)
        return {"lang": lang}

    @app.post("/api/plan/refresh")
    async def api_plan_refresh() -> dict[str, Any]:
        """Ask for the limits immediately: the usual tick is every five minutes."""
        return {"plan": await asyncio.to_thread(plan_limits.refresh)}

    @app.get("/api/sessions")
    async def api_sessions(
        limit: int = 100,
        project: str | None = None,
        status: str | None = None,
        period: str | None = None,
    ) -> dict[str, Any]:
        """The "Sessions" screen: filters by project, status and period (task C1)."""
        conn = open_db()
        try:
            return {
                "sessions": sessions_page(
                    conn,
                    project=project,
                    status=status,
                    since=period_start(period),
                    limit=limit,
                ),
                "projects": [dict(row) for row in known_projects(conn)],
            }
        finally:
            conn.close()

    @app.get("/api/sessions/{session_id}")
    async def api_session(session_id: str) -> dict[str, Any]:
        conn = open_db()
        try:
            summary = session_summary(conn, session_id)
            if summary is None:
                raise HTTPException(status_code=404, detail="session not found")
            return {
                "session": vars(summary) | {"cache_write": summary.cache_write},
                "models": [
                    {"model": model, "turns": turns, "output_tokens": output}
                    for model, turns, output in session_models(conn, session_id)
                ],
                "tools": [
                    {"tool": tool, "calls": calls}
                    for tool, calls in session_tools(conn, session_id)
                ],
                # How much time went into each tool is known to telemetry (milestone E).
                "tool_times": session_tool_times(conn, session_id),
                # The work line: resume scatters one piece of work over several sessions.
                "chain": session_chain(conn, session_id),
                # The "Session" screen (task C2): the context chart and the turn feed.
                "turns": session_turns(conn, session_id),
                "events": session_events(conn, session_id),
            }
        finally:
            conn.close()

    @app.get("/api/sessions/{session_id}/prompts")
    async def api_prompts(session_id: str, limit: int = 500) -> dict[str, Any]:
        """The whole prompt log of a session (task C7).

        The card shows the ends of it; the rest is asked for by a click, so a screen
        with twenty tips does not carry every prompt of every session along.
        """
        conn = open_db()
        try:
            return {"session_id": session_id, "prompts": session_prompts(conn, session_id, limit)}
        finally:
            conn.close()

    @app.post("/api/sessions/{session_id}/hide")
    async def api_hide(session_id: str, hidden: bool = True) -> dict[str, Any]:
        """Remove a session from the dashboard or bring it back."""
        conn = connect(db_path, apply_schema=False)
        try:
            if not set_hidden(conn, session_id, hidden):
                raise HTTPException(status_code=404, detail="session not found")
        finally:
            conn.close()
        return {"session_id": session_id, "hidden": hidden}

    @app.post("/api/sessions/{session_id}/close")
    async def api_close(session_id: str) -> dict[str, Any]:
        """Terminate the session process and remove it from the dashboard.

        The process comes from `claude agents --json` - that is the exact link to the
        `sessionId`. If the session is not there, it has already finished: we just remove
        the card.
        """
        conn = connect(db_path, apply_schema=False)
        try:
            if session_summary(conn, session_id) is None:
                raise HTTPException(status_code=404, detail="session not found")
            process = await asyncio.to_thread(process_for_session, session_id)
            stopped = terminate(process.pid) if process is not None else False
            set_hidden(conn, session_id, True)
        finally:
            conn.close()
        return {
            "session_id": session_id,
            "hidden": True,
            "stopped": stopped,
            "pid": process.pid if process is not None else None,
            "note": None if stopped else "the process is gone - the session was hidden",
        }

    @app.post("/otlp/v1/{signal}")
    async def otlp_export(signal: str, request: Request) -> Response:
        """The receiver of official Claude Code telemetry (milestone E, SPEC §2).

        Claude Code sends OTLP/JSON payloads here when its environment has
        `OTEL_EXPORTER_OTLP_ENDPOINT` pointed at this address (`cburn otel` prints the
        variables needed). It occupies no port of its own: telemetry arrives at the same
        localhost:8799 as the dashboard.

        The answer is always empty JSON - that is what a successful export looks like by
        protocol. A receiver switched off in the config acknowledges the payload too: a
        4xx would make the exporter pile up retries and complain into the Claude Code log.
        """
        if signal not in otlp.SIGNALS:
            raise HTTPException(status_code=404, detail="unknown OTLP signal")
        if not config.load().get("otel", {}).get("enabled", True):
            return JSONResponse({})
        body = await request.body()
        try:
            payload = otlp.decode(body, request.headers.get("content-encoding"))
        except Exception:
            # Usually this is `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`: payloads
            # arrive and we do not understand them. Swallowing them silently is not an
            # option - then `cburn otel` would say "no payloads arrived" and the human
            # would hunt in the wrong place. A mark in the loss counter shows the truth.
            log.warning("OTLP payload (%s) not parsed: %s bytes", signal, len(body))
            await asyncio.to_thread(_note_undecodable, open_db, signal)
            return JSONResponse({})

        def write() -> dict[str, int]:
            # An SQLite connection belongs to the thread it was created in.
            conn = connect(db_path, apply_schema=False)
            try:
                return otlp.ingest(conn, signal, payload)
            finally:
                conn.close()

        try:
            stats = await asyncio.to_thread(write)
        except sqlite3.OperationalError as exc:
            # SQLite has a single writer: if a long transaction runs nearby (a full
            # reindex), the queue may not wait it out. Refusing with a 503 is more honest
            # than a silent acknowledgement - the exporter will repeat the payload itself.
            log.warning("OTLP payload (%s) not stored: %s", signal, exc)
            return JSONResponse({}, status_code=503)
        if stats["dropped"]:
            log.info("OTLP (%s): chunks not parsed - %s", signal, stats["dropped"])
        return JSONResponse({})

    @app.get("/api/otel")
    async def api_otel() -> dict[str, Any]:
        """What arrived over telemetry: receptions, metrics and events (milestone E)."""
        conn = open_db()
        try:
            return otlp.status(conn) | {"enabled": config.load().get("otel", {}).get("enabled")}
        finally:
            conn.close()

    @app.get("/api/health")
    async def api_health() -> dict[str, Any]:
        return {"ok": True, "clients": hub.size, "now": datetime.now(UTC).isoformat()}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await hub.join(socket)
        await socket.send_json({"type": "overview", "data": await collect_overview()})
        try:
            while True:  # incoming messages are not needed, we wait for the disconnect
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await hub.leave(socket)

    _mount_frontend(app)
    return app


#: A refusal to carry an act out, by reason. A stale plan and a file changed since are
#: not errors of the request but a conflict: the file is no longer what the human saw.
ACT_CODES = {"stale": 409, "changed_since": 409, "already_rolled_back": 409, "no_rollback": 409}


def _act_code(exc: actions.ActError) -> int:
    """The reason goes out as a dictionary key: the words are the frontend's business."""
    if exc.reason == "not_found":
        return 404
    return ACT_CODES.get(exc.reason, 400)


def _default_liveness() -> dict[str, datetime | None] | None:
    return live_state(use_cache=True)


def _close_pending(open_db: Any, ids: dict[str, datetime | None] | None) -> int:
    """The pending closes in their own connection (task D7)."""
    conn = open_db()
    try:
        return actions.run_pending(conn, ids)
    finally:
        conn.close()


async def _refresh_liveness(open_db: Any, probe: LivenessProbe) -> None:
    """One pass reconciling `is_live` with the list of Claude Code processes (task B4).

    The poll is synchronous and not fast (about 1.3 s), so it goes into a thread: the
    event loop must keep handing out the overview every second.
    """
    try:
        ids = await asyncio.to_thread(probe)
        conn = open_db()
        try:
            changed = refresh_liveness(conn, ids)
        finally:
            conn.close()
        if changed:
            log.info("session liveness: %s updated", changed)
        # The same pass carries out the closes the human agreed to: it already knows which
        # sessions are alive and what their processes are doing, and a session is closed in
        # a pause between steps rather than in the middle of one (task D7). Into a thread
        # with a connection of its own: an SQLite object belongs to the thread it was
        # created in, and terminating goes through the slow `claude agents --json`.
        closed = await asyncio.to_thread(_close_pending, open_db, ids)
        if closed:
            log.info("sessions closed by an accepted tip: %s", closed)
    except asyncio.CancelledError:
        raise
    except Exception:  # a background task must not fail silently
        log.exception("could not refresh session liveness")


async def _liveness(open_db: Any, probe: LivenessProbe) -> None:
    """Periodic liveness reconciliation."""
    while True:
        await asyncio.sleep(LIVENESS_SECONDS)
        await _refresh_liveness(open_db, probe)


#: How often to remove stale telemetry. Less often than a day makes no sense: the
#: retention is counted in days, and the deletion costs fractions of a second.
PRUNE_SECONDS = 24 * 3600


def _note_undecodable(open_db: Any, signal: str) -> None:
    """Mark an incomprehensible payload in the reception counters (milestone E)."""
    conn = open_db()
    try:
        otlp.note_ingest(conn, signal, stored=0, dropped=1)
    except sqlite3.OperationalError as exc:  # the database is busy - the mark is not worth a retry
        log.warning("the mark about an incomprehensible payload was not stored: %s", exc)
    finally:
        conn.close()


def _prune_once(open_db: Any, keep_days: int) -> dict[str, int]:
    """One cleanup in its own connection: an SQLite object belongs to the thread."""
    conn = open_db()
    try:
        return otlp.prune(conn, keep_days)
    finally:
        conn.close()


async def _prune_otel(open_db: Any) -> None:
    """Telemetry cleanup by retention: right away and once a day afterwards."""
    while True:
        try:
            # Reading the config is inside too: the file is edited by hand, and a typo in
            # `keep_days` must not quietly kill the background task forever.
            keep_days = int((config.load().get("otel") or {}).get("keep_days") or 0)
            await asyncio.to_thread(_prune_once, open_db, keep_days)
        except asyncio.CancelledError:
            raise
        except Exception:  # a background task must not fail silently
            log.exception("could not remove stale telemetry")
        await asyncio.sleep(PRUNE_SECONDS)


async def _ticker(hub: Hub, collect: Any) -> None:
    """The periodic overview: the instrument readings must not freeze during pauses."""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        if hub.size == 0:  # nobody to show it to - nothing to compute either
            continue
        payload = await _collect(collect)
        if payload is not None:
            await hub.broadcast({"type": "overview", "data": payload})


async def _collect(collect: Any) -> dict[str, Any] | None:
    try:
        return await collect()
    except Exception:
        log.exception("could not build the overview")
        return None


async def _pump(events: asyncio.Queue[IngestStats], hub: Hub, collect: Any) -> None:
    """An event from the watcher -> a fresh overview for every subscriber."""
    while True:
        await events.get()
        # A burst of events in a row is collapsed: the overview is computed whole anyway.
        while not events.empty():
            events.get_nowait()
        payload = await _collect(collect)
        if payload is not None:
            await hub.broadcast({"type": "overview", "data": payload})


class Frontend(StaticFiles):
    """Frontend statics with different cache policies for the shell and the assets.

    The browser must revalidate `index.html` every time: otherwise after a rebuild it
    takes the old shell from the cache and loads a bundle that does not exist. The assets
    themselves are named by Vite with a content hash, so they can be cached forever.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        elif path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built frontend if it exists (task A6)."""
    if (WEB_DIST / "index.html").exists():
        app.mount("/", Frontend(directory=WEB_DIST, html=True), name="web")
        return

    @app.get("/")
    async def placeholder() -> dict[str, Any]:
        return {
            "cburn": "the frontend is not built yet",
            "how to build": "cd web && npm install && npm run build",
            "api": ["/api/overview", "/api/sessions", "/api/plan/refresh", "/api/health", "/ws"],
        }
