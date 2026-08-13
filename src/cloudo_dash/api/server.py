"""HTTP и WebSocket на 127.0.0.1 (задача A5, ТЗ §5).

Наружу сервер не смотрит: слушает только localhost. Фронт общается с бэком
исключительно через эти эндпоинты — прямых обращений к файловой системе у него
нет, иначе обёртка Tauri на M5 потребовала бы переделки.

Watcher живёт в том же процессе: он в своём потоке дочитывает транскрипты и
через `on_ingest` толкает событие в очередь asyncio, откуда оно уходит всем
подписчикам `/ws`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .. import paths
from ..collector.indexer import IngestStats
from ..collector.watcher import TranscriptWatcher
from ..db import connect
from ..metrics import (
    overview,
    recent_sessions,
    session_models,
    session_summary,
    session_tools,
)

log = logging.getLogger(__name__)

#: Как часто обзор уходит подписчикам, даже когда в транскриптах тихо. Без
#: этого стрелка замирала бы на последнем ходе: окна burn rate скользят, и в
#: паузе расход должен падать сам. Пересчёт обзора стоит около 2 мс, так что
#: секундный такт ничего не нагружает.
TICK_SECONDS = 1.0

#: Собранный фронт (задача A6). Пока его нет, отдаётся заглушка.
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class Hub:
    """Подписчики WebSocket и рассылка им событий."""

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
            except Exception:  # отвалившийся клиент не должен ломать рассылку
                await self.leave(socket)

    @property
    def size(self) -> int:
        return len(self._clients)


def create_app(
    *, db_path: Path | None = None, projects_dir: Path | None = None, watch: bool = True
) -> FastAPI:
    """Собрать приложение. `watch=False` — для тестов, где watcher не нужен."""
    hub = Hub()
    events: asyncio.Queue[IngestStats] = asyncio.Queue()

    def open_db() -> Any:
        return connect(db_path, apply_schema=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        connect(db_path).close()  # схема должна существовать до первого запроса
        loop = asyncio.get_running_loop()
        watcher: TranscriptWatcher | None = None
        pump: asyncio.Task[None] | None = None
        if watch:
            root = projects_dir or paths.CLAUDE_PROJECTS_DIR

            def publish(stats: IngestStats) -> None:
                # Вызывается в потоке watcher, а очередь принадлежит циклу
                # asyncio — отсюда call_soon_threadsafe.
                loop.call_soon_threadsafe(events.put_nowait, stats)

            watcher = TranscriptWatcher(root, db_path=db_path, on_ingest=publish)
            watcher.start()
            pump = asyncio.create_task(_pump(events, hub, open_db))
        # Тикер не зависит от watcher: окна burn rate скользят и без новых ходов.
        ticker = asyncio.create_task(_ticker(hub, open_db))
        try:
            yield
        finally:
            if watcher is not None:
                watcher.stop()
            for task in (pump, ticker):
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    app = FastAPI(title="cloudo-dash", lifespan=lifespan)

    @app.get("/api/overview")
    async def api_overview() -> dict[str, Any]:
        conn = open_db()
        try:
            return overview(conn)
        finally:
            conn.close()

    @app.get("/api/sessions")
    async def api_sessions(limit: int = 50) -> dict[str, Any]:
        conn = open_db()
        try:
            return {"sessions": [dict(row) for row in recent_sessions(conn, limit)]}
        finally:
            conn.close()

    @app.get("/api/sessions/{session_id}")
    async def api_session(session_id: str) -> dict[str, Any]:
        conn = open_db()
        try:
            summary = session_summary(conn, session_id)
            if summary is None:
                raise HTTPException(status_code=404, detail="сессия не найдена")
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
            }
        finally:
            conn.close()

    @app.get("/api/health")
    async def api_health() -> dict[str, Any]:
        return {"ok": True, "clients": hub.size, "now": datetime.now(UTC).isoformat()}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await hub.join(socket)
        conn = open_db()
        try:
            await socket.send_json({"type": "overview", "data": overview(conn)})
        finally:
            conn.close()
        try:
            while True:  # входящие сообщения не нужны, ждём разрыва
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await hub.leave(socket)

    _mount_frontend(app)
    return app


async def _ticker(hub: Hub, open_db: Any) -> None:
    """Периодический обзор: показания прибора не должны застывать в паузах."""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        if hub.size == 0:  # некому показывать — незачем и считать
            continue
        payload = _collect(open_db)
        if payload is not None:
            await hub.broadcast({"type": "overview", "data": payload})


def _collect(open_db: Any) -> dict[str, Any] | None:
    conn = open_db()
    try:
        return overview(conn)
    except Exception:
        log.exception("не удалось собрать обзор")
        return None
    finally:
        conn.close()


async def _pump(events: asyncio.Queue[IngestStats], hub: Hub, open_db: Any) -> None:
    """Событие от watcher → свежий обзор всем подписчикам."""
    while True:
        await events.get()
        # Пачку событий подряд схлопываем: обзор всё равно считается целиком.
        while not events.empty():
            events.get_nowait()
        payload = _collect(open_db)
        if payload is not None:
            await hub.broadcast({"type": "overview", "data": payload})


def _mount_frontend(app: FastAPI) -> None:
    """Раздать собранный фронт, если он есть (задача A6)."""
    if (WEB_DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
        return

    @app.get("/")
    async def placeholder() -> dict[str, Any]:
        return {
            "cloudo-dash": "фронт ещё не собран",
            "как собрать": "cd web && npm install && npm run build",
            "api": ["/api/overview", "/api/sessions", "/api/health", "/ws"],
        }
