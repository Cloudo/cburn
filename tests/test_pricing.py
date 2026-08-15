"""Tests of the cost calculation (task B1)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cburn import pricing
from cburn.collector.indexer import ingest_file
from cburn.db import connect

PRICES = {
    "prices": {
        "claude-opus-5": {
            "input": 5.0,
            "output": 25.0,
            "cache_write_5m": 6.25,
            "cache_write_1h": 10.0,
            "cache_read": 0.5,
        }
    }
}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "test.db")


def assistant(
    message_id: str,
    *,
    model: str = "claude-opus-5",
    output: int = 1_000_000,
    cache_read: int = 1_000_000,
    write_1h: int = 1_000_000,
    write_5m: int = 1_000_000,
    input_tokens: int = 1_000_000,
) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "uuid": f"u-{message_id}",
            "sessionId": "s1",
            "timestamp": "2026-08-13T10:00:00Z",
            "requestId": f"req_{message_id}",
            "cwd": "/Users/x/project",
            "message": {
                "id": message_id,
                "model": model,
                "content": [{"type": "text", "text": "..."}],
                "usage": {
                    "input_tokens": input_tokens,
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


def write_transcript(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def test_cost_counted_on_ingest(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Config prices are applied to a turn right at import time."""
    pricing.sync_prices(conn, PRICES)
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1")])

    ingest_file(conn, path)

    # A million tokens of each part - the cost equals the sum of the rates.
    cost = conn.execute("SELECT cost_usd FROM turns WHERE message_id = 'msg_1'").fetchone()[0]
    assert cost == pytest.approx(5.0 + 25.0 + 0.5 + 6.25 + 10.0)
    assert conn.execute("SELECT cost_usd FROM sessions WHERE id = 's1'").fetchone()[
        0
    ] == pytest.approx(cost)


def test_unknown_model_costs_zero(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A model without a price does not invent a rate, it honestly costs zero."""
    pricing.sync_prices(conn, PRICES)
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1", model="claude-unknown-9")])

    ingest_file(conn, path)

    assert conn.execute("SELECT cost_usd FROM turns").fetchone()[0] == 0
    assert pricing.unknown_models(conn) == [{"model": "claude-unknown-9", "turns": 1}]


def test_dated_model_matches_price(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """`claude-haiku-4-5-20251001` is billed as `claude-haiku-4-5`."""
    pricing.sync_prices(
        conn, {"prices": {"claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.1}}}
    )
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(
        path,
        [
            assistant(
                "msg_1",
                model="claude-haiku-4-5-20251001",
                output=1_000_000,
                input_tokens=0,
                cache_read=0,
                write_1h=0,
                write_5m=0,
            )
        ],
    )

    ingest_file(conn, path)

    assert conn.execute("SELECT cost_usd FROM turns").fetchone()[0] == pytest.approx(5.0)
    assert pricing.unknown_models(conn) == []


def test_recalculate_after_price_change(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """A price change recomputes the already imported history."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1")])
    ingest_file(conn, path)
    assert conn.execute("SELECT cost_usd FROM turns").fetchone()[0] == 0  # there were no prices yet

    pricing.recalculate(conn, PRICES)

    assert conn.execute("SELECT cost_usd FROM turns").fetchone()[0] > 0
    assert conn.execute("SELECT cost_usd FROM sessions").fetchone()[0] > 0


def test_empty_prices_keep_table(conn: sqlite3.Connection) -> None:
    """An empty config section does not wipe the prices already entered."""
    pricing.sync_prices(conn, PRICES)

    assert pricing.sync_prices(conn, {"prices": {}}) == 0
    assert [row["model"] for row in pricing.known_prices(conn)] == ["claude-opus-5"]


def test_sample_prices_are_readable() -> None:
    """The template for `cburn prices --init` parses and covers every column."""
    sample = pricing.sample_prices()

    assert "claude-opus-5" in sample
    assert set(sample["claude-opus-5"]) == {
        "input",
        "output",
        "cache_write_5m",
        "cache_write_1h",
        "cache_read",
    }
