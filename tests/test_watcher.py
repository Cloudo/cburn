"""Tests of watching the transcripts (task A4)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from cburn.collector.indexer import IngestStats
from cburn.collector.watcher import TranscriptWatcher

DEBOUNCE = 0.05
TIMEOUT = 10.0


def assistant(message_id: str, *, session: str = "s1", uuid: str = "u1", output: int = 42) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "sessionId": session,
            "timestamp": "2026-08-13T10:00:00Z",
            "cwd": "/Users/x/project",
            "message": {
                "id": message_id,
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": "..."}],
                "usage": {"output_tokens": output, "cache_read_input_tokens": 10},
            },
        }
    )


def append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(line + "\n")
        fh.flush()


def wait_for(condition, timeout: float = TIMEOUT) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def count_turns(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0])
    except sqlite3.OperationalError:  # the schema does not exist yet
        return 0
    finally:
        conn.close()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    projects = tmp_path / "projects" / "project"
    projects.mkdir(parents=True)
    return projects


# --- the main A4 check ----------------------------------------------------------


def test_append_to_transcript_creates_turn(root: Path, tmp_path: Path) -> None:
    """An appended line leads to a turn appearing in the database."""
    db_path = tmp_path / "watch.db"
    seen: list[IngestStats] = []
    watcher = TranscriptWatcher(
        root.parent, debounce=DEBOUNCE, db_path=db_path, on_ingest=seen.append
    )
    with watcher:
        append(root / "s1.jsonl", assistant("msg_1"))
        assert wait_for(lambda: count_turns(db_path) == 1), "the turn did not reach the database"

        append(root / "s1.jsonl", assistant("msg_2", uuid="u2"))
        assert wait_for(lambda: count_turns(db_path) == 2), "the second turn did not appear"

    assert seen, "the on_ingest handler was never called"
    assert sum(stats.turns_new for stats in seen) == 2


def test_new_file_in_new_project_is_picked_up(root: Path, tmp_path: Path) -> None:
    """A project directory created after the start is watched too."""
    db_path = tmp_path / "watch.db"
    with TranscriptWatcher(root.parent, debounce=DEBOUNCE, db_path=db_path):
        append(root.parent / "new-project" / "s2.jsonl", assistant("msg_1", session="s2"))
        assert wait_for(lambda: count_turns(db_path) == 1)


def test_initial_scan_reads_what_accumulated_offline(root: Path, tmp_path: Path) -> None:
    """Whatever was written before the start is read at startup."""
    append(root / "s1.jsonl", assistant("msg_1"))
    db_path = tmp_path / "watch.db"
    with TranscriptWatcher(root.parent, debounce=DEBOUNCE, db_path=db_path):
        assert wait_for(lambda: count_turns(db_path) == 1)


def test_initial_scan_can_be_skipped(root: Path, tmp_path: Path) -> None:
    append(root / "s1.jsonl", assistant("msg_1"))
    db_path = tmp_path / "watch.db"
    watcher = TranscriptWatcher(root.parent, debounce=DEBOUNCE, db_path=db_path)
    watcher.start(initial_scan=False)
    try:
        assert watcher.wait_idle(TIMEOUT)
        assert count_turns(db_path) == 0
    finally:
        watcher.stop()


# --- the queue and the debounce ---------------------------------------------------


def test_debounce_collapses_burst_of_events(root: Path, tmp_path: Path) -> None:
    """A burst of events on one file is parsed in a single pass."""
    db_path = tmp_path / "watch.db"
    passes: list[IngestStats] = []
    watcher = TranscriptWatcher(root.parent, debounce=0.3, db_path=db_path, on_ingest=passes.append)
    with watcher:
        path = root / "s1.jsonl"
        for index in range(5):
            append(path, assistant(f"msg_{index}", uuid=f"u{index}"))
            time.sleep(0.02)
        assert wait_for(lambda: count_turns(db_path) == 5)

    assert len(passes) <= 2, f"the debounce did not gather the burst: {len(passes)} passes"


def test_queue_holds_until_debounce_elapses(tmp_path: Path) -> None:
    """A file is not taken into work before its time (a queue check without the FS)."""
    watcher = TranscriptWatcher(tmp_path, debounce=10.0)
    watcher.enqueue([tmp_path / "s1.jsonl"])
    assert watcher._due(time.monotonic()) == []
    assert watcher._due(time.monotonic() + 11) == [tmp_path / "s1.jsonl"]


def test_only_jsonl_is_queued(tmp_path: Path) -> None:
    """Foreign files in the Claude Code directory are ignored."""
    watcher = TranscriptWatcher(tmp_path, debounce=0)
    watcher.enqueue([tmp_path / "s1.jsonl", tmp_path / "note.txt", tmp_path / "db.sqlite"])
    assert watcher._due(time.monotonic() + 1) == [tmp_path / "s1.jsonl"]


def test_repeated_events_do_not_multiply_queue(tmp_path: Path) -> None:
    watcher = TranscriptWatcher(tmp_path, debounce=0)
    for _ in range(10):
        watcher.enqueue([tmp_path / "s1.jsonl"])
    assert len(watcher._due(time.monotonic() + 1)) == 1


# --- resilience -------------------------------------------------------------------


def test_broken_line_does_not_stop_watching(root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "watch.db"
    with TranscriptWatcher(root.parent, debounce=DEBOUNCE, db_path=db_path):
        append(root / "s1.jsonl", "{broken json")
        append(root / "s1.jsonl", assistant("msg_1"))
        assert wait_for(lambda: count_turns(db_path) == 1)


def test_failing_callback_does_not_stop_watching(root: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "watch.db"

    def boom(stats: IngestStats) -> None:
        raise RuntimeError("the handler failed")

    with TranscriptWatcher(root.parent, debounce=DEBOUNCE, db_path=db_path, on_ingest=boom):
        append(root / "s1.jsonl", assistant("msg_1"))
        assert wait_for(lambda: count_turns(db_path) == 1)
        append(root / "s1.jsonl", assistant("msg_2", uuid="u2"))
        assert wait_for(lambda: count_turns(db_path) == 2)


def test_stop_is_idempotent(root: Path, tmp_path: Path) -> None:
    watcher = TranscriptWatcher(root.parent, debounce=DEBOUNCE, db_path=tmp_path / "watch.db")
    watcher.start()
    watcher.stop()
    watcher.stop()


def test_claude_dir_is_not_written_to(root: Path, tmp_path: Path) -> None:
    """The invariant: nothing appears or changes inside the transcript directory."""
    path = root / "s1.jsonl"
    append(path, assistant("msg_1"))
    before = {p: p.stat().st_mtime_ns for p in sorted(root.parent.rglob("*"))}

    with TranscriptWatcher(root.parent, debounce=DEBOUNCE, db_path=tmp_path / "watch.db"):
        assert wait_for(lambda: count_turns(tmp_path / "watch.db") == 1)

    after = {p: p.stat().st_mtime_ns for p in sorted(root.parent.rglob("*"))}
    assert after == before


def test_connection_lives_in_worker_thread(root: Path, tmp_path: Path) -> None:
    """SQLite does not tolerate a connection across threads - it is created in the worker."""
    db_path = tmp_path / "watch.db"
    errors: list[BaseException] = []
    original = TranscriptWatcher._ingest

    def guard(self, conn, path):  # type: ignore[no-untyped-def]
        try:
            return original(self, conn, path)
        except BaseException as exc:  # pragma: no cover - a test safety net
            errors.append(exc)
            raise

    TranscriptWatcher._ingest = guard  # type: ignore[method-assign]
    try:
        with TranscriptWatcher(root.parent, debounce=DEBOUNCE, db_path=db_path):
            append(root / "s1.jsonl", assistant("msg_1"))
            assert wait_for(lambda: count_turns(db_path) == 1)
    finally:
        TranscriptWatcher._ingest = original  # type: ignore[method-assign]
    assert not errors
