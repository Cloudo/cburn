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
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from .. import config, paths, pricing
from ..collector.indexer import IngestStats
from ..collector.watcher import TranscriptWatcher
from ..db import connect
from ..limits import LimitsWatcher
from ..metrics import (
    known_projects,
    overview,
    period_start,
    refresh_liveness,
    session_chain,
    session_models,
    session_summary,
    session_tools,
    sessions_page,
    set_hidden,
)
from ..processes import active_session_ids, process_for_session, terminate

log = logging.getLogger(__name__)

#: Как часто обзор уходит подписчикам, даже когда в транскриптах тихо. Без
#: этого стрелка замирала бы на последнем ходе: окна burn rate скользят, и в
#: паузе расход должен падать сам. Пересчёт обзора стоит около 2 мс, так что
#: секундный такт ничего не нагружает.
TICK_SECONDS = 1.0

#: Как часто сверять сессии со списком процессов Claude Code. Опрос стоит около
#: 1,3 с, а сессии не появляются и не заканчиваются чаще (задача B4).
LIVENESS_SECONDS = 15.0

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
    *,
    db_path: Path | None = None,
    projects_dir: Path | None = None,
    watch: bool = True,
    limits: LimitsWatcher | None = None,
    liveness: Callable[[], set[str] | None] | None = None,
) -> FastAPI:
    """Собрать приложение.

    `watch=False` — для тестов, где watcher не нужен; `limits` и `liveness`
    подменяются там же, чтобы тесты не ходили ни в связку ключей, ни в сеть,
    ни в `claude agents --json`.
    """
    hub = Hub()
    events: asyncio.Queue[IngestStats] = asyncio.Queue()
    # Лимиты живут отдельно от БД: они приходят от Anthropic, а не из транскриптов.
    plan_limits = limits or LimitsWatcher()

    def open_db() -> Any:
        return connect(db_path, apply_schema=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with connect(db_path) as conn:  # схема должна существовать до первого запроса
            pricing.recalculate(conn, config.load())
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
            pump = asyncio.create_task(_pump(events, hub, collect_overview))
        # Тикер не зависит от watcher: окна burn rate скользят и без новых ходов.
        ticker = asyncio.create_task(_ticker(hub, collect_overview))
        ask_live = liveness or _default_liveness
        # Первый проход — до первого запроса: иначе живая сессия успеет
        # мигнуть «закончилась».
        await _refresh_liveness(open_db, ask_live)
        probe = asyncio.create_task(_liveness(open_db, ask_live))
        try:
            yield
        finally:
            if watcher is not None:
                watcher.stop()
            for task in (pump, ticker, probe):
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    app = FastAPI(title="cloudo-dash", lifespan=lifespan)

    async def collect_overview() -> dict[str, Any]:
        conn = open_db()
        try:
            payload = overview(conn)
        finally:
            conn.close()
        # Запрос к Anthropic блокирующий и раз в пять минут — в поток.
        payload["plan"] = await asyncio.to_thread(plan_limits.current)
        return payload

    @app.get("/api/overview")
    async def api_overview() -> dict[str, Any]:
        return await collect_overview()

    @app.post("/api/plan/refresh")
    async def api_plan_refresh() -> dict[str, Any]:
        """Спросить лимиты немедленно: обычный такт — раз в пять минут."""
        return {"plan": await asyncio.to_thread(plan_limits.refresh)}

    @app.get("/api/sessions")
    async def api_sessions(
        limit: int = 100,
        project: str | None = None,
        status: str | None = None,
        period: str | None = None,
    ) -> dict[str, Any]:
        """Экран «Сессии»: фильтры по проекту, статусу и периоду (задача C1)."""
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
                # Линия работы: resume рассыпает одну работу по нескольким сессиям.
                "chain": session_chain(conn, session_id),
            }
        finally:
            conn.close()

    @app.post("/api/sessions/{session_id}/hide")
    async def api_hide(session_id: str, hidden: bool = True) -> dict[str, Any]:
        """Убрать сессию с дашборда или вернуть её обратно."""
        conn = connect(db_path, apply_schema=False)
        try:
            if not set_hidden(conn, session_id, hidden):
                raise HTTPException(status_code=404, detail="сессия не найдена")
        finally:
            conn.close()
        return {"session_id": session_id, "hidden": hidden}

    @app.post("/api/sessions/{session_id}/close")
    async def api_close(session_id: str) -> dict[str, Any]:
        """Завершить процесс сессии и убрать её с дашборда.

        Процесс берётся из `claude agents --json` — это точная связка с
        `sessionId`. Если сессии там нет, она уже закончилась: просто убираем
        карточку.
        """
        conn = connect(db_path, apply_schema=False)
        try:
            if session_summary(conn, session_id) is None:
                raise HTTPException(status_code=404, detail="сессия не найдена")
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
            "note": None if stopped else "процесс уже не запущен — сессия убрана с дашборда",
        }

    @app.get("/api/health")
    async def api_health() -> dict[str, Any]:
        return {"ok": True, "clients": hub.size, "now": datetime.now(UTC).isoformat()}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await hub.join(socket)
        await socket.send_json({"type": "overview", "data": await collect_overview()})
        try:
            while True:  # входящие сообщения не нужны, ждём разрыва
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await hub.leave(socket)

    _mount_frontend(app)
    return app


def _default_liveness() -> set[str] | None:
    return active_session_ids(use_cache=False)


async def _refresh_liveness(open_db: Any, probe: Callable[[], set[str] | None]) -> None:
    """Один проход сверки `is_live` со списком процессов Claude Code (задача B4).

    Опрос синхронный и не быстрый (около 1,3 с), поэтому уходит в поток: цикл
    событий должен успевать раздавать обзор каждую секунду.
    """
    try:
        ids = await asyncio.to_thread(probe)
        conn = open_db()
        try:
            changed = refresh_liveness(conn, ids)
        finally:
            conn.close()
        if changed:
            log.info("живость сессий: обновлено %s", changed)
    except asyncio.CancelledError:
        raise
    except Exception:  # фоновая задача не должна падать молча
        log.exception("не удалось обновить живость сессий")


async def _liveness(open_db: Any, probe: Callable[[], set[str] | None]) -> None:
    """Периодическая сверка живости."""
    while True:
        await asyncio.sleep(LIVENESS_SECONDS)
        await _refresh_liveness(open_db, probe)


async def _ticker(hub: Hub, collect: Any) -> None:
    """Периодический обзор: показания прибора не должны застывать в паузах."""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        if hub.size == 0:  # некому показывать — незачем и считать
            continue
        payload = await _collect(collect)
        if payload is not None:
            await hub.broadcast({"type": "overview", "data": payload})


async def _collect(collect: Any) -> dict[str, Any] | None:
    try:
        return await collect()
    except Exception:
        log.exception("не удалось собрать обзор")
        return None


async def _pump(events: asyncio.Queue[IngestStats], hub: Hub, collect: Any) -> None:
    """Событие от watcher → свежий обзор всем подписчикам."""
    while True:
        await events.get()
        # Пачку событий подряд схлопываем: обзор всё равно считается целиком.
        while not events.empty():
            events.get_nowait()
        payload = await _collect(collect)
        if payload is not None:
            await hub.broadcast({"type": "overview", "data": payload})


class Frontend(StaticFiles):
    """Статика фронта с разной политикой кеша для оболочки и ассетов.

    `index.html` браузер обязан сверять каждый раз: иначе после пересборки он
    берёт из кеша старую оболочку и грузит несуществующий бандл. Сами ассеты
    Vite именует с хешем содержимого, поэтому их можно кешировать навсегда.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        elif path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _mount_frontend(app: FastAPI) -> None:
    """Раздать собранный фронт, если он есть (задача A6)."""
    if (WEB_DIST / "index.html").exists():
        app.mount("/", Frontend(directory=WEB_DIST, html=True), name="web")
        return

    @app.get("/")
    async def placeholder() -> dict[str, Any]:
        return {
            "cloudo-dash": "фронт ещё не собран",
            "как собрать": "cd web && npm install && npm run build",
            "api": ["/api/overview", "/api/sessions", "/api/plan/refresh", "/api/health", "/ws"],
        }
