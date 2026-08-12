"""Слежение за каталогом транскриптов (задача A4).

Claude Code дописывает JSONL пачками по несколько строк на ход, поэтому события
файловой системы приходят очередями. Путь ставится в очередь и дочитывается не
раньше чем через `debounce` секунд после последнего события по нему — так один
ход не разбирается по три раза.

Читает и пишет всё один рабочий поток: соединение SQLite создаётся внутри него,
наружу отдаются только готовые `IngestStats` через `on_ingest`. Каталог Claude
Code открывается только на чтение.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..db import connect
from .indexer import IngestStats, ingest_file

log = logging.getLogger(__name__)

#: Пауза после последнего события по файлу, ТЗ §3 — «дебаунс ~200 мс».
DEFAULT_DEBOUNCE = 0.2

#: Как часто рабочий поток просыпается смотреть на очередь.
TICK = 0.05

TRANSCRIPT_SUFFIX = ".jsonl"


class TranscriptWatcher:
    """Наблюдатель за `~/.claude/projects`, дочитывающий хвосты транскриптов."""

    def __init__(
        self,
        root: Path,
        *,
        debounce: float = DEFAULT_DEBOUNCE,
        on_ingest: Callable[[IngestStats], None] | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.root = root
        self.debounce = debounce
        self.on_ingest = on_ingest
        self._db_path = db_path
        self._pending: dict[Path, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._observer: Observer | None = None  # type: ignore[valid-type]
        self._worker: threading.Thread | None = None
        self._idle = threading.Event()  # очередь разобрана, полезно тестам
        self._idle.set()

    # --- жизненный цикл -----------------------------------------------------

    def start(self, *, initial_scan: bool = True) -> None:
        """Запустить слежение. `initial_scan` дочитывает то, что накопилось офлайн."""
        if initial_scan:
            self.enqueue(self.root.rglob(f"*{TRANSCRIPT_SUFFIX}"), delay=0)
        self._worker = threading.Thread(target=self._run, name="cdash-watcher", daemon=True)
        self._worker.start()
        self._observer = Observer()
        self._observer.schedule(_Handler(self), str(self.root), recursive=True)
        self._observer.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout)
            self._observer = None
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout)
            self._worker = None

    def __enter__(self) -> TranscriptWatcher:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # --- очередь ------------------------------------------------------------

    def enqueue(self, paths: Iterable[Path], delay: float | None = None) -> None:
        """Поставить файлы в очередь на дочитывание."""
        due_delay = self.debounce if delay is None else delay
        now = time.monotonic()
        with self._lock:
            for path in paths:
                if path.suffix == TRANSCRIPT_SUFFIX:
                    self._pending[path] = now + due_delay
            if self._pending:
                self._idle.clear()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Дождаться, пока очередь разобрана (нужно тестам и остановке сервера)."""
        return self._idle.wait(timeout)

    def _due(self, now: float) -> list[Path]:
        with self._lock:
            ready = [path for path, due in self._pending.items() if due <= now]
            for path in ready:
                del self._pending[path]
            return ready

    # --- рабочий поток ------------------------------------------------------

    def _run(self) -> None:
        conn = connect(self._db_path)
        try:
            while not self._stop.is_set():
                ready = self._due(time.monotonic())
                if not ready:
                    with self._lock:
                        if not self._pending:
                            self._idle.set()
                    self._stop.wait(TICK)
                    continue
                for path in ready:
                    self._ingest(conn, path)
        finally:
            conn.close()

    def _ingest(self, conn: sqlite3.Connection, path: Path) -> None:
        try:
            stats = ingest_file(conn, path)
        except Exception:  # один битый файл не должен ронять слежение
            log.exception("не удалось дочитать %s", path)
            return
        if stats.lines and self.on_ingest is not None:
            try:
                self.on_ingest(stats)
            except Exception:
                log.exception("обработчик on_ingest упал на %s", path)


class _Handler(FileSystemEventHandler):
    """Переводит события ФС в постановку файла в очередь."""

    def __init__(self, watcher: TranscriptWatcher) -> None:
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type == "opened":
            return
        paths = [event.src_path]
        # Переименование внутри каталога: дочитывать нужно файл под новым именем.
        dest = getattr(event, "dest_path", None)
        if dest:
            paths.append(dest)
        self._watcher.enqueue(
            Path(path.decode() if isinstance(path, bytes) else path) for path in paths
        )
