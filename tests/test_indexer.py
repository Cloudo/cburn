"""Tests of importing transcripts into SQLite (task A2)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cburn import paths as indexer_paths
from cburn.collector.indexer import RAW_SAMPLE_LIMIT, ingest_file, ingest_tree
from cburn.db import connect
from cburn.metrics import session_chain

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "transcripts").glob("*.jsonl"))


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "test.db")


def assistant(
    message_id: str,
    *,
    session: str = "s1",
    ts: str = "2026-08-13T10:00:00Z",
    uuid: str = "u1",
    output: int = 100,
    cache_read: int = 1000,
    write_1h: int = 50,
    write_5m: int = 0,
    content: list[dict] | None = None,
    model: str = "claude-opus-5",
    sidechain: bool = False,
) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "sessionId": session,
            "timestamp": ts,
            "requestId": f"req_{message_id}",
            "isSidechain": sidechain,
            "cwd": "/Users/x/project",
            "message": {
                "id": message_id,
                "model": model,
                "content": content or [{"type": "text", "text": "..."}],
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": output,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": write_1h,
                        "ephemeral_5m_input_tokens": write_5m,
                    },
                },
            },
        }
    )


def prompt(text: str, *, session: str = "s1", ts: str = "2026-08-13T09:00:00Z") -> str:
    return json.dumps(
        {
            "type": "user",
            "uuid": f"p-{text[:8]}",
            "sessionId": session,
            "timestamp": ts,
            "cwd": "/Users/x/project",
            "message": {"content": text},
        }
    )


def write_transcript(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines))


def rows(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    return list(conn.execute(sql))


# --- the A2 readiness criterion ------------------------------------------------


def test_second_pass_changes_nothing(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A repeated call on the same file changes not a single row."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [prompt("hello"), assistant("msg_1"), assistant("msg_2", uuid="u2")])

    first = ingest_file(conn, path)
    snapshot = rows(conn, "SELECT * FROM turns ORDER BY message_id")
    second = ingest_file(conn, path)

    assert first.turns_new == 2
    assert second.turns_new == 0
    assert second.lines == 0  # the tail is read, there is nothing to re-read
    assert [tuple(row) for row in rows(conn, "SELECT * FROM turns ORDER BY message_id")] == [
        tuple(row) for row in snapshot
    ]


def test_reingest_after_offset_reset_is_idempotent(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Even when the file is re-read from scratch, turns are not doubled."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1"), assistant("msg_2", uuid="u2")])
    ingest_file(conn, path)
    conn.execute("UPDATE files SET offset = 0")  # simulating an offset reset
    conn.commit()

    stats = ingest_file(conn, path)

    assert stats.lines == 2
    assert stats.turns_new == 0
    assert stats.turns_known == 2
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2


# --- a turn is assembled from several records ----------------------------------


def test_turn_split_across_records_is_one_row(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Three records of one answer give one turn and one usage, not three."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(
        path,
        [
            assistant("msg_1", uuid="u1", content=[{"type": "thinking", "thinking": "..."}]),
            assistant("msg_1", uuid="u2", content=[{"type": "text", "text": "..."}]),
            assistant(
                "msg_1",
                uuid="u3",
                content=[
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls -la"}}
                ],
            ),
        ],
    )
    stats = ingest_file(conn, path)

    assert stats.turns_new == 1
    turn = rows(conn, "SELECT * FROM turns")[0]
    assert turn["output_tokens"] == 100  # not 300
    assert turn["cache_read"] == 1000
    assert turn["uuid"] == "u1"  # uuid of the turn's first record
    tools = rows(conn, "SELECT * FROM tool_calls")
    assert [(row["tool"], row["detail"]) for row in tools] == [("Bash", "ls")]


def test_turn_blocks_split_between_passes(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Turn blocks read on the next pass do not create a second turn."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1", uuid="u1")])
    ingest_file(conn, path)

    with path.open("a") as fh:
        fh.write(
            assistant(
                "msg_1",
                uuid="u2",
                content=[
                    {"type": "tool_use", "id": "t9", "name": "Read", "input": {"file_path": "/x"}}
                ],
            )
            + "\n"
        )
    stats = ingest_file(conn, path)

    assert stats.turns_new == 0
    assert stats.turns_known == 1
    assert stats.tools_new == 1
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    assert conn.execute("SELECT SUM(output_tokens) FROM turns").fetchone()[0] == 100


def test_tool_calls_are_not_duplicated(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A repeated import of the same tool_use block is swallowed by tool_use_id."""
    path = tmp_path / "proj" / "s1.jsonl"
    block = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status"}}]
    write_transcript(path, [assistant("msg_1", content=block)])
    ingest_file(conn, path)
    conn.execute("UPDATE files SET offset = 0")
    conn.commit()
    ingest_file(conn, path)

    assert conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 1
    assert rows(conn, "SELECT detail FROM tool_calls")[0]["detail"] == "git status"


# --- resume: turn copies in another file ---------------------------------------


def test_resume_copy_does_not_double_count(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A turn copied on resume stays with the first session and does not double the spend."""
    original = tmp_path / "proj" / "s1.jsonl"
    write_transcript(original, [assistant("msg_1", session="s1", output=100)])
    ingest_file(conn, original)

    resumed = tmp_path / "proj" / "s2.jsonl"
    write_transcript(
        resumed,
        [
            assistant("msg_1", session="s2", output=100),  # a copy of a past turn
            assistant("msg_2", session="s2", uuid="u2", output=7),  # a new turn
        ],
    )
    stats = ingest_file(conn, resumed)

    assert stats.turns_new == 1
    assert stats.turns_known == 1
    assert conn.execute("SELECT SUM(output_tokens) FROM turns").fetchone()[0] == 107
    totals = {
        row["id"]: row["tokens_out"] for row in rows(conn, "SELECT id, tokens_out FROM sessions")
    }
    assert totals == {"s1": 100, "s2": 7}


def test_same_uuid_in_two_files_is_not_a_conflict(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A record uuid is not unique across history - failing on it is not allowed."""
    for name, session in (("s1.jsonl", "s1"), ("s2.jsonl", "s2")):
        path = tmp_path / "proj" / name
        write_transcript(path, [assistant(f"msg_{session}", session=session, uuid="same-uuid")])
        ingest_file(conn, path)
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2


# --- incrementality -------------------------------------------------------------


def test_incremental_tail_read(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1")])
    first = ingest_file(conn, path)

    with path.open("a") as fh:
        fh.write(assistant("msg_2", uuid="u2") + "\n")
    second = ingest_file(conn, path)

    assert second.lines == 1  # only the appended line was read
    assert second.offset > first.offset
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2


def test_partial_line_is_not_consumed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """An unfinished line waits for the next pass and does not move the offset."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1")])
    ingest_file(conn, path)

    tail = assistant("msg_2", uuid="u2")
    with path.open("a") as fh:
        fh.write(tail[:40])  # the write is cut off midway
    stats = ingest_file(conn, path)
    assert stats.lines == 0
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1

    with path.open("a") as fh:
        fh.write(tail[40:] + "\n")
    stats = ingest_file(conn, path)
    assert stats.lines == 1
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2


def test_offset_resets_on_truncate(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1"), assistant("msg_2", uuid="u2")])
    ingest_file(conn, path)

    write_transcript(path, [assistant("msg_3", uuid="u3")])  # the file is truncated and rewritten
    stats = ingest_file(conn, path)

    assert stats.restarted
    assert stats.lines == 1
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 3


def test_offset_resets_on_new_inode(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1"), assistant("msg_2", uuid="u2")])
    ingest_file(conn, path)

    replacement = tmp_path / "proj" / "replacement.jsonl"
    write_transcript(
        replacement,
        [assistant("msg_1"), assistant("msg_2", uuid="u2"), assistant("msg_3", uuid="u3")],
    )
    replacement.replace(path)  # recreating the file: a different inode, a bigger size
    stats = ingest_file(conn, path)

    assert stats.restarted
    assert stats.lines == 3
    assert stats.turns_new == 1
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 3


def test_missing_file_is_survivable(conn: sqlite3.Connection, tmp_path: Path) -> None:
    stats = ingest_file(conn, tmp_path / "no-such.jsonl")
    assert stats.lines == 0


def test_broken_lines_do_not_stop_the_pass(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1"), "{broken json", "", assistant("msg_2", uuid="u2")])
    stats = ingest_file(conn, path)
    assert stats.lines == 4
    assert stats.turns_new == 2


# --- sessions and projects ------------------------------------------------------


def test_session_metadata_and_totals(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "my-project" / "s1.jsonl"
    write_transcript(
        path,
        [
            prompt("first question", ts="2026-08-13T09:00:00Z"),
            assistant("msg_1", ts="2026-08-13T09:00:05Z", output=10, cache_read=500, write_1h=20),
            prompt("second question", ts="2026-08-13T09:01:00Z"),
            assistant(
                "msg_2",
                uuid="u2",
                ts="2026-08-13T09:01:05Z",
                output=30,
                cache_read=900,
                write_1h=0,
                write_5m=5,
            ),
        ],
    )
    ingest_file(conn, path)

    session = rows(conn, "SELECT * FROM sessions")[0]
    assert session["first_prompt"] == "first question"
    assert session["started_at"] == "2026-08-13T09:00:00Z"
    assert session["last_at"] == "2026-08-13T09:01:05Z"
    assert session["turns"] == 2
    assert session["tokens_out"] == 40
    assert session["cache_read"] == 1400
    assert session["cache_write"] == 25
    assert session["last_context"] == 2 + 900 + 5  # the context of the last turn

    project = rows(conn, "SELECT * FROM projects")[0]
    assert project["slug"] == "my-project"
    assert project["root_path"] == "/Users/x/project"
    assert project["display_name"] == "project"  # the screen gets the name, not the slug
    assert session["project_id"] == project["id"]


def test_first_prompt_ignores_sidechain_and_compact_summary(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    sidechain = json.loads(prompt("a task for the subagent", ts="2026-08-13T08:00:00Z"))
    sidechain["isSidechain"] = True
    compact = json.loads(prompt("a retelling", ts="2026-08-13T08:30:00Z"))
    compact["isCompactSummary"] = True
    write_transcript(
        path,
        [
            json.dumps(sidechain),
            json.dumps(compact),
            prompt("a real prompt", ts="2026-08-13T09:00:00Z"),
        ],
    )
    ingest_file(conn, path)
    assert rows(conn, "SELECT first_prompt FROM sessions")[0]["first_prompt"] == "a real prompt"


def test_several_sessions_in_one_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A file is not a session: the grouping follows the record field."""
    path = tmp_path / "proj" / "mixed.jsonl"
    write_transcript(
        path,
        [
            assistant("msg_1", session="s1", output=10),
            assistant("msg_2", session="s2", uuid="u2", output=20),
        ],
    )
    stats = ingest_file(conn, path)

    assert stats.sessions == 2
    totals = {
        row["id"]: row["tokens_out"] for row in rows(conn, "SELECT id, tokens_out FROM sessions")
    }
    assert totals == {"s1": 10, "s2": 20}


def test_sidechain_turn_is_marked(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1", sidechain=True)])
    ingest_file(conn, path)
    assert rows(conn, "SELECT is_sidechain FROM turns")[0]["is_sidechain"] == 1


def test_synthetic_records_are_skipped(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """`<synthetic>` is a service answer with zero usage, it is not a turn."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(
        path,
        [
            assistant("msg_1", output=10),
            assistant("msg_syn", uuid="u2", model="<synthetic>", output=0, cache_read=0),
        ],
    )
    stats = ingest_file(conn, path)
    assert stats.turns_new == 1
    assert rows(conn, "SELECT message_id FROM turns")[0]["message_id"] == "msg_1"


def test_unknown_types_do_not_break_the_turn(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """An unknown record is counted, stashed into raw_events and does not disturb the turn."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(
        path,
        [json.dumps({"type": "attachment", "sessionId": "s1", "uuid": "a1"}), assistant("msg_1")],
    )
    stats = ingest_file(conn, path)
    assert stats.unknown == 1
    assert stats.turns_new == 1
    stored = conn.execute("SELECT type, payload FROM raw_events").fetchone()
    assert stored["type"] == "attachment"
    assert "a1" in stored["payload"]


# --- fixtures -------------------------------------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_fixture_ingest_matches_manual_count(
    conn: sqlite3.Connection, path: Path, tmp_path: Path
) -> None:
    """The database totals match an independent count over the raw JSON."""
    target = tmp_path / "proj" / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(path.read_text())
    ingest_file(conn, target)

    by_message: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record.get("type") != "assistant" or record["message"].get("model") == "<synthetic>":
            continue
        by_message[record["message"]["id"]] = record["message"].get("usage") or {}
    expected_out = sum(usage.get("output_tokens", 0) for usage in by_message.values())
    expected_read = sum(usage.get("cache_read_input_tokens", 0) for usage in by_message.values())

    row = conn.execute(
        "SELECT COUNT(*) AS turns, SUM(output_tokens) AS out, SUM(cache_read) AS read FROM turns"
    ).fetchone()
    assert row["turns"] == len(by_message)
    assert row["out"] == expected_out
    assert row["read"] == expected_read


def test_ingest_tree_walks_all_files(conn: sqlite3.Connection, tmp_path: Path) -> None:
    for index, name in enumerate(("a", "b")):
        write_transcript(
            tmp_path / f"proj-{name}" / "s.jsonl", [assistant(f"msg_{name}", session=f"s{index}")]
        )
    results = ingest_tree(conn, tmp_path)
    assert len(results) == 2
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2


def test_subagent_transcript_keeps_parent_project(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subagents live in `<project>/<session>/subagents/` - the parent project is used."""
    monkeypatch.setattr(indexer_paths, "CLAUDE_PROJECTS_DIR", tmp_path)
    project = tmp_path / "-Users-me-proj"
    write_transcript(project / "s1.jsonl", [assistant("msg_1", session="s1")])
    write_transcript(
        project / "s1" / "subagents" / "agent-a1.jsonl",
        [assistant("msg_2", session="s1", sidechain=True)],
    )

    ingest_tree(conn, tmp_path)

    slugs = [row[0] for row in conn.execute("SELECT slug FROM projects")]
    assert slugs == ["-Users-me-proj"]
    row = conn.execute(
        "SELECT p.slug FROM sessions s JOIN projects p ON p.id = s.project_id WHERE s.id = 's1'"
    ).fetchone()
    assert row["slug"] == "-Users-me-proj"


def test_ingest_tree_reports_progress(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """The walk reports progress per file - the CLI draws its line from it (task B2)."""
    for name in ("a", "b", "c"):
        write_transcript(tmp_path / f"proj-{name}" / "s.jsonl", [assistant(f"msg_{name}")])
    seen: list[tuple[int, int, str]] = []

    ingest_tree(
        conn,
        tmp_path,
        on_file=lambda done, total, path: seen.append((done, total, path.parent.name)),
    )

    assert [(done, total) for done, total, _ in seen] == [(1, 3), (2, 3), (3, 3)]
    assert [name for _, _, name in seen] == ["proj-a", "proj-b", "proj-c"]


# --- unknown records (task B6) ---------------------------------------------------


def test_unknown_records_counted_with_limited_samples(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """The counter grows on every record, the payload is kept only for the first ones."""
    unknown = [
        json.dumps({"type": "attachment", "version": "2.1.228", "sessionId": "s1"})
        for _ in range(RAW_SAMPLE_LIMIT + 4)
    ]
    write_transcript(tmp_path / "proj" / "s1.jsonl", [assistant("msg_1"), *unknown])

    ingest_file(conn, tmp_path / "proj" / "s1.jsonl")

    seen = conn.execute(
        "SELECT seen FROM raw_event_counts WHERE type = 'attachment' AND version = '2.1.228'"
    ).fetchone()[0]
    assert seen == RAW_SAMPLE_LIMIT + 4
    assert conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0] == RAW_SAMPLE_LIMIT


def test_unknown_records_split_by_version(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A (type, version) pair is counted separately: the format changes between versions."""
    write_transcript(
        tmp_path / "proj" / "s1.jsonl",
        [
            json.dumps({"type": "mode", "version": "2.1.220", "sessionId": "s1"}),
            json.dumps({"type": "mode", "version": "2.1.231", "sessionId": "s1"}),
            json.dumps({"type": "mode", "sessionId": "s1"}),  # no version at all
        ],
    )

    ingest_file(conn, tmp_path / "proj" / "s1.jsonl")

    rows = dict(conn.execute("SELECT version, seen FROM raw_event_counts WHERE type = 'mode'"))
    assert rows == {"2.1.220": 1, "2.1.231": 1, "": 1}


# --- resume forks (task B5) ------------------------------------------------------


def test_resume_links_child_to_parent(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Turns copied on resume link the new session to the original one."""
    write_transcript(
        tmp_path / "proj" / "s1.jsonl",
        [
            assistant("msg_1", session="s1", uuid="u1", ts="2026-08-13T10:00:00Z"),
            assistant("msg_2", session="s1", uuid="u2", ts="2026-08-13T10:05:00Z"),
        ],
    )
    # Resume: past turns are copied into a new file with a new sessionId.
    write_transcript(
        tmp_path / "proj" / "s2.jsonl",
        [
            assistant("msg_1", session="s2", uuid="u1", ts="2026-08-13T10:00:00Z"),
            assistant("msg_2", session="s2", uuid="u2", ts="2026-08-13T10:05:00Z"),
            assistant("msg_3", session="s2", uuid="u3", ts="2026-08-13T11:00:00Z"),
        ],
    )

    ingest_tree(conn, tmp_path)

    parents = dict(conn.execute("SELECT id, parent_session_id FROM sessions"))
    assert parents == {"s1": None, "s2": "s1"}
    # The copies do not double the spend: the turn stays with the first session.
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 3


def test_resume_direction_does_not_depend_on_read_order(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """The parent is whoever started earlier, not whose file was read first."""
    later = tmp_path / "proj" / "s2.jsonl"
    earlier = tmp_path / "proj" / "s1.jsonl"
    write_transcript(
        later,
        [
            assistant("msg_1", session="s2", uuid="u1", ts="2026-08-13T10:00:00Z"),
            assistant("msg_3", session="s2", uuid="u3", ts="2026-08-13T11:00:00Z"),
        ],
    )
    write_transcript(
        earlier,
        [assistant("msg_1", session="s1", uuid="u1", ts="2026-08-13T10:00:00Z")],
    )

    ingest_file(conn, later)  # the child first
    ingest_file(conn, earlier)

    parents = dict(conn.execute("SELECT id, parent_session_id FROM sessions"))
    assert parents["s2"] == "s1"
    assert parents["s1"] is None


def test_session_chain_sums_the_whole_line(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A work line collects every session of the chain and their totals (task B5)."""
    write_transcript(
        tmp_path / "proj" / "s1.jsonl",
        [assistant("msg_1", session="s1", uuid="u1", ts="2026-08-13T10:00:00Z", output=100)],
    )
    write_transcript(
        tmp_path / "proj" / "s2.jsonl",
        [
            assistant("msg_1", session="s2", uuid="u1", ts="2026-08-13T10:00:00Z", output=100),
            assistant("msg_2", session="s2", uuid="u2", ts="2026-08-13T11:00:00Z", output=200),
        ],
    )
    ingest_tree(conn, tmp_path)

    chain = session_chain(conn, "s2")

    assert sorted(chain["sessions"]) == ["s1", "s2"]
    assert chain["turns"] == 2  # the copy is not counted twice


# --- incomplete usage in turn records --------------------------------------------


def test_zero_usage_record_does_not_hide_real_one(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A turn started by a zero record gets its spend from the finishing one."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(
        path,
        [
            assistant("msg_1", uuid="u1", output=0, cache_read=0, write_1h=0),
            assistant("msg_1", uuid="u2", output=106, cache_read=5000, write_1h=7),
        ],
    )
    stats = ingest_file(conn, path)

    assert stats.turns_new == 1
    turn = rows(conn, "SELECT * FROM turns")[0]
    assert (turn["output_tokens"], turn["cache_read"], turn["cache_write_1h"]) == (106, 5000, 7)


def test_real_usage_arrives_in_later_pass(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """The turn is in the database with a zero: the read tail raises the spend, not doubles it."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1", uuid="u1", output=0, cache_read=0, write_1h=0)])
    ingest_file(conn, path)
    assert rows(conn, "SELECT output_tokens FROM turns")[0]["output_tokens"] == 0

    with path.open("a") as fh:
        fh.write(assistant("msg_1", uuid="u2", output=106, cache_read=5000, write_1h=7) + "\n")
    stats = ingest_file(conn, path)

    assert stats.turns_new == 0
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    assert rows(conn, "SELECT output_tokens FROM turns")[0]["output_tokens"] == 106
    assert rows(conn, "SELECT tokens_out FROM sessions")[0]["tokens_out"] == 106


def test_empty_resume_copy_does_not_erase_usage(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A copy of a turn with zero spend does not overwrite the one already counted."""
    original = tmp_path / "proj" / "s1.jsonl"
    write_transcript(original, [assistant("msg_1", session="s1", output=100)])
    ingest_file(conn, original)

    copy = tmp_path / "proj" / "s2.jsonl"
    write_transcript(copy, [assistant("msg_1", session="s2", output=0, cache_read=0, write_1h=0)])
    ingest_file(conn, copy)

    assert rows(conn, "SELECT output_tokens FROM turns")[0]["output_tokens"] == 100


# --- how the session ended --------------------------------------------------------


def test_pending_state_after_prompt(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A prompt without an answer means a request is running right now."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1"), prompt("new question", ts="2026-08-13T11:00:00Z")])
    ingest_file(conn, path)

    session = rows(conn, "SELECT * FROM sessions")[0]
    assert session["last_record_kind"] == "prompt"
    assert session["last_record_at"] == "2026-08-13T11:00:00Z"


def test_pending_state_clears_after_answer(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [prompt("question", ts="2026-08-13T11:00:00Z")])
    ingest_file(conn, path)
    assert rows(conn, "SELECT last_record_kind FROM sessions")[0]["last_record_kind"] == "prompt"

    with path.open("a") as fh:
        fh.write(assistant("msg_1", ts="2026-08-13T11:00:20Z") + "\n")
    ingest_file(conn, path)

    session = rows(conn, "SELECT * FROM sessions")[0]
    assert session["last_record_kind"] == "assistant"
    assert session["last_record_at"] == "2026-08-13T11:00:20Z"


def test_service_records_do_not_clear_pending(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Between a prompt and an answer, attachments and other service records are written."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(
        path,
        [
            prompt("question", ts="2026-08-13T11:00:00Z"),
            json.dumps(
                {
                    "type": "attachment",
                    "sessionId": "s1",
                    "uuid": "a1",
                    "timestamp": "2026-08-13T11:00:05Z",
                }
            ),
        ],
    )
    ingest_file(conn, path)
    assert rows(conn, "SELECT last_record_kind FROM sessions")[0]["last_record_kind"] == "prompt"


def test_service_records_keep_last_record_at(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A batch of one service record must not wipe the time of the last real record."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1", ts="2026-08-13T11:00:00Z")])
    ingest_file(conn, path)

    with path.open("a") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "attachment",
                    "sessionId": "s1",
                    "uuid": "a1",
                    "timestamp": "2026-08-13T11:05:00Z",
                }
            )
            + "\n"
        )
    ingest_file(conn, path)

    session = rows(conn, "SELECT * FROM sessions")[0]
    assert session["last_record_kind"] == "assistant"
    assert session["last_record_at"] == "2026-08-13T11:00:00Z"
    assert session["last_at"] == "2026-08-13T11:05:00Z"


# --- the session title -------------------------------------------------------------


def title_record(title: str, *, kind: str = "ai-title", session: str = "s1") -> str:
    field = "aiTitle" if kind == "ai-title" else "customTitle"
    return json.dumps({"type": kind, field: title, "sessionId": session})


def test_ai_title_becomes_session_name(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1"), title_record("Review ROADMAP next tasks")])
    ingest_file(conn, path)

    session = rows(conn, "SELECT * FROM sessions")[0]
    assert session["title"] == "Review ROADMAP next tasks"
    assert session["title_source"] == "ai"


def test_custom_title_wins_over_generated(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A title set by a human is not overwritten by a generated one."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(
        path,
        [
            assistant("msg_1"),
            title_record("own name", kind="custom-title"),
            title_record("generated"),
        ],
    )
    ingest_file(conn, path)

    session = rows(conn, "SELECT * FROM sessions")[0]
    assert (session["title"], session["title_source"]) == ("own name", "custom")

    with path.open("a") as fh:
        fh.write(title_record("another generated one") + "\n")
    ingest_file(conn, path)
    assert rows(conn, "SELECT title FROM sessions")[0]["title"] == "own name"


def test_title_record_does_not_create_pending_state(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A title record has no time - it must not move the session state."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1", ts="2026-08-13T10:00:00Z"), title_record("name")])
    ingest_file(conn, path)

    session = rows(conn, "SELECT * FROM sessions")[0]
    assert session["last_record_kind"] == "assistant"
    assert session["last_at"] == "2026-08-13T10:00:00Z"
