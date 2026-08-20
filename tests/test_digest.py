"""Tests of the period digest (task D1).

The main test here is about privacy: not a single word of the conversation may seep
into the digest. The rest checks that the advisor has something to lean on:
heavy sessions, resume chains, mechanical work on Opus.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cburn import pricing
from cburn.analyzer import digest
from cburn.collector.indexer import ingest_tree
from cburn.db import connect

SECRET = "a secret word from the conversation"


def assistant(
    message_id: str,
    *,
    session: str = "s1",
    ts: datetime | None = None,
    uuid: str = "u1",
    model: str = "claude-opus-5",
    output: int = 100,
    cache_read: int = 60_000,
    cache_write_5m: int = 0,
    tools: list[dict] | None = None,
) -> str:
    stamp = (ts or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
    content: list[dict] = [{"type": "text", "text": SECRET}]
    for index, tool in enumerate(tools or []):
        content.append(
            {
                "type": "tool_use",
                "id": f"{message_id}-{index}",
                "name": tool["name"],
                "input": tool.get("input", {}),
            }
        )
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "sessionId": session,
            "timestamp": stamp,
            "requestId": f"req_{message_id}",
            "cwd": "/Users/x/secret-project",
            "message": {
                "id": message_id,
                "model": model,
                "content": content,
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": output,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": 1000,
                        "ephemeral_5m_input_tokens": cache_write_5m,
                    },
                },
            },
        }
    )


def prompt(text: str, *, session: str = "s1", uuid: str = "p1") -> str:
    return json.dumps(
        {
            "type": "user",
            "uuid": uuid,
            "sessionId": session,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "message": {"role": "user", "content": text},
        }
    )


def title(text: str, *, session: str = "s1") -> str:
    return json.dumps({"type": "ai-title", "sessionId": session, "aiTitle": text})


def test_cache_counts_writes_that_could_not_be_read(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A five-minute write is wasted when the next turn comes later than its five minutes."""
    now = datetime.now(UTC)
    seed(
        conn,
        tmp_path,
        [
            # the pause after it is ten minutes: nothing could have read this back
            assistant("m1", uuid="u1", ts=now - timedelta(minutes=30), cache_write_5m=500),
            # a minute later comes the next turn - this one paid for itself
            assistant("m2", uuid="u2", ts=now - timedelta(minutes=20), cache_write_5m=500),
            # the last of the session, and long enough ago that its turn will not come
            assistant("m3", uuid="u3", ts=now - timedelta(minutes=19), cache_write_5m=500),
        ],
    )
    section = digest._cache(conn, now - timedelta(hours=1), now, None)
    assert section["write_5m"] == 1500
    assert section["expired_5m"] == 1000, "the first and the last write are the wasted ones"
    assert section["pauses"] == 2
    assert section["expired_share"] == pytest.approx(0.667, abs=0.001)


def test_cache_leaves_the_tail_of_the_period_alone(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """A write from a minute ago is not wasted - its turn has simply not come yet."""
    now = datetime.now(UTC)
    seed(conn, tmp_path, [assistant("m1", ts=now - timedelta(minutes=1), cache_write_5m=800)])
    section = digest._cache(conn, now - timedelta(hours=1), now, None)
    assert section["write_5m"] == 800
    assert section["expired_5m"] == 0


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "digest.db")


def seed(conn: sqlite3.Connection, tmp_path: Path, lines: list[str], name: str = "s.jsonl") -> None:
    root = tmp_path / "projects" / "-Users-x-secret-project"
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text("\n".join(lines) + "\n")
    ingest_tree(conn, tmp_path / "projects")


def test_digest_carries_no_conversation_text(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """No prompt, no answer, no session title - only numbers and names."""
    seed(
        conn,
        tmp_path,
        [
            prompt("please fix the SSO login for the client Daisy Ltd"),
            title("fixing the SSO login"),
            assistant("msg_1", tools=[{"name": "Bash", "input": {"command": "grep -r pass ."}}]),
        ],
    )

    payload = json.dumps(
        digest.build(conn, datetime.now(UTC) - timedelta(days=1)), ensure_ascii=False
    )

    assert SECRET not in payload
    assert "Daisy" not in payload
    assert "SSO" not in payload, "session titles are a retelling of the conversation"
    assert "password" not in payload, "command arguments are dropped back at parse time"
    assert "grep" in payload, "the command name stays visible"


def test_digest_fits_the_limit(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """The digest fits into 20k tokens - every top list is trimmed for that."""
    now = datetime.now(UTC)
    lines = [
        assistant(
            f"msg_{index}",
            uuid=f"u{index}",
            session=f"s{index % 40}",
            ts=now - timedelta(minutes=index),
            tools=[{"name": "Bash", "input": {"command": f"tool{index % 60} run"}}],
        )
        for index in range(400)
    ]
    seed(conn, tmp_path, lines)

    payload = digest.build(conn, now - timedelta(days=1))

    assert payload["size"]["within_limit"]
    assert payload["size"]["tokens_approx"] < digest.TOKEN_LIMIT


def test_digest_marks_heavy_sessions_and_chains(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A heavy session and a work line are visible to the advisor."""
    now = datetime.now(UTC)
    seed(
        conn,
        tmp_path,
        [assistant("msg_1", session="base", ts=now - timedelta(hours=2), cache_read=400_000)],
    )
    seed(
        conn,
        tmp_path,
        [
            assistant("msg_1", session="resumed", ts=now - timedelta(hours=2)),
            assistant("msg_2", session="resumed", uuid="u2", ts=now - timedelta(minutes=10)),
        ],
        name="second.jsonl",
    )

    payload = digest.build(
        conn,
        now - timedelta(days=1),
        config={"thresholds": {"context_crit": 150_000}},
    )

    heavy = {row["id"]: row for row in payload["sessions"]}
    assert heavy["base"]["over_context_limit"] is True
    assert [chain["root"] for chain in payload["chains"]] == ["base"]
    assert payload["chains"][0]["sessions"] == 2


def test_digest_counts_mechanical_opus(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A turn that held only reading and searching is a candidate for a simpler model."""
    now = datetime.now(UTC)
    seed(
        conn,
        tmp_path,
        [
            assistant("msg_1", ts=now - timedelta(minutes=5), tools=[{"name": "Read"}]),
            assistant("msg_2", uuid="u2", ts=now - timedelta(minutes=4), tools=[{"name": "Edit"}]),
        ],
    )
    pricing.sync_prices(conn, {"prices": {"claude-opus-5": {"input": 5.0, "output": 25.0}}})
    pricing.apply_costs(conn)

    payload = digest.build(conn, now - timedelta(days=1))

    assert payload["mechanical_opus"]["turns"] == 1
    assert payload["mechanical_opus"]["opus_turns"] == 1
    assert payload["mechanical_opus"]["share"] == 1.0


def test_digest_sees_heredoc_calls(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A script driven through a heredoc is counted apart from ordinary calls."""
    now = datetime.now(UTC)
    seed(
        conn,
        tmp_path,
        [
            assistant(
                f"msg_{index}",
                uuid=f"u{index}",
                ts=now - timedelta(minutes=index),
                tools=[{"name": "Bash", "input": {"command": "python3 - <<'PY'\nprint(1)\nPY"}}],
            )
            for index in range(3)
        ],
    )

    payload = digest.build(conn, now - timedelta(days=1))

    assert payload["tools"]["heredoc_calls"] == 3
    assert any(row["command"] == "python3 <<" for row in payload["tools"]["bash_commands"])
