"""Watching the transcript directory (task A4).

Claude Code appends JSONL in bunches of several lines per turn, so filesystem events
arrive in queues. A path is queued and read no earlier than `debounce` seconds after the
last event on it - that way one turn is not parsed three times over.

A single worker thread does all the reading and writing: the SQLite connection is created
inside it, and only finished `IngestStats` leave through `on_ingest`. The Claude Code
directory is opened read-only.
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

#: The pause after the last event on a file, TZ §3 - "debounce ~200 ms".
DEFAULT_DEBOUNCE = 0.2

#: How often the worker thread wakes up to look at the queue.
TICK = 0.05

TRANSCRIPT_SUFFIX = ".jsonl"


class TranscriptWatcher:
    """A watcher over `~/.claude/projects` that reads transcript tails."""

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
        self._idle = threading.Event()  # queue drained, useful for tests
        self._idle.set()

    # --- lifecycle ----------------------------------------------------------

    def start(self, *, initial_scan: bool = True) -> None:
        """Start watching. `initial_scan` reads whatever piled up offline."""
        if initial_scan:
            self.enqueue(self.root.rglob(f"*{TRANSCRIPT_SUFFIX}"), delay=0)
        self._worker = threading.Thread(target=self._run, name="cburn-watcher", daemon=True)
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

    # --- queue --------------------------------------------------------------

    def enqueue(self, paths: Iterable[Path], delay: float | None = None) -> None:
        """Queue files to be read."""
        due_delay = self.debounce if delay is None else delay
        now = time.monotonic()
        with self._lock:
            for path in paths:
                if path.suffix == TRANSCRIPT_SUFFIX:
                    self._pending[path] = now + due_delay
            if self._pending:
                self._idle.clear()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Wait until the queue is drained (needed by tests and by server shutdown)."""
        return self._idle.wait(timeout)

    def _due(self, now: float) -> list[Path]:
        with self._lock:
            ready = [path for path, due in self._pending.items() if due <= now]
            for path in ready:
                del self._pending[path]
            return ready

    # --- worker thread --------------------------------------------------------

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
        except Exception:  # one broken file must not bring the watching down
            log.exception("could not read %s", path)
            return
        if stats.lines and self.on_ingest is not None:
            try:
                self.on_ingest(stats)
            except Exception:
                log.exception("the on_ingest handler failed on %s", path)


class _Handler(FileSystemEventHandler):
    """Turns FS events into queueing a file."""

    def __init__(self, watcher: TranscriptWatcher) -> None:
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type == "opened":
            return
        paths = [event.src_path]
        # A rename inside the directory: the file has to be read under its new name.
        dest = getattr(event, "dest_path", None)
        if dest:
            paths.append(dest)
        self._watcher.enqueue(
            Path(path.decode() if isinstance(path, bytes) else path) for path in paths
        )
