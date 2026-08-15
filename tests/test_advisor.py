"""Advisor tests (task D2).

The real `claude -p` is never called: it costs money and goes to the network. A runner
of our own is substituted instead, returning an envelope of the same shape the CLI
actually returns - the envelope shape was checked against the installed Claude Code.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cburn.analyzer import advisor
from cburn.db import connect

DIGEST = {"period": {"since": "2026-08-13T00:00:00+00:00", "until": "2026-08-13T12:00:00+00:00"}}


def envelope(advice: list[dict], cost: float = 0.021) -> dict:
    """The `--output-format json` envelope exactly as the CLI returns it."""
    return {
        "is_error": False,
        "num_turns": 2,
        "stop_reason": "tool_use",
        "total_cost_usd": cost,
        "result": json.dumps({"advice": advice}, ensure_ascii=False),
        "structured_output": {"advice": advice},
        "modelUsage": {"claude-haiku-4-5-20251001": {"costUSD": cost}},
    }


def runner_for(payload: dict) -> object:
    def run(prompt: str, model: str, budget_usd: float) -> dict:
        run.prompt = prompt  # type: ignore[attr-defined]
        return payload

    return run


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "advice.db")


def test_command_matches_installed_cli(conn: sqlite3.Connection) -> None:
    """The flags are those present in the installed version; `--max-turns` is not among them."""
    command = advisor.build_command("haiku", budget_usd=0.05)

    assert command[:4] == ["claude", "-p", "--output-format", "json"]
    assert "--max-turns" not in command, "the flag is gone, turns are bounded by empty --tools"
    assert command[command.index("--model") + 1] == "haiku"
    assert command[command.index("--max-budget-usd") + 1] == "0.05"
    # Tools are off: the advisor looks at a ready digest.
    assert command[command.index("--tools") + 1] == ""
    schema = json.loads(command[command.index("--json-schema") + 1])
    assert schema["properties"]["advice"]["items"]["required"] == [
        "title",
        "severity",
        "detail",
        "action",
        "evidence",
    ]


def test_advice_without_evidence_is_dropped(conn: sqlite3.Connection) -> None:
    """A tip without support in numbers is general words, we do not store those (TZ §6)."""
    payload = envelope(
        [
            {
                "title": "Watch the context",
                "severity": "warn",
                "detail": "",
                "action": "",
                "evidence": "",
            },
            {
                "title": "Move file reading to haiku",
                "severity": "warn",
                "detail": "212 turns with Read and Grep only",
                "action": "put haiku on mechanical turns",
                "evidence": "mechanical_opus.turns = 212, opus_cost_usd = 59.3",
            },
        ]
    )

    result = advisor.advise(conn, DIGEST, runner=runner_for(payload))

    assert [item["title"] for item in result["advice"]] == ["Move file reading to haiku"]
    rows = conn.execute("SELECT title, evidence, status FROM advice_items").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "new"


def test_run_is_recorded_with_its_own_cost(conn: sqlite3.Connection) -> None:
    """The tick's own cost is visible honestly - it is written into advice."""
    payload = envelope(
        [
            {
                "title": "Close the overgrown work line",
                "severity": "crit",
                "detail": "19 sessions in one chain",
                "action": "start a new session",
                "evidence": "chains[0].sessions = 19, cost_usd = 244",
            }
        ],
        cost=0.0234,
    )

    result = advisor.advise(conn, DIGEST, runner=runner_for(payload))

    row = conn.execute("SELECT * FROM advice").fetchone()
    assert row["cost_usd"] == pytest.approx(0.0234)
    assert row["max_severity"] == "crit"
    assert row["model"] == "claude-haiku-4-5-20251001", "history gets the full name, not the alias"
    assert json.loads(row["digest_json"])["period"]["since"] == DIGEST["period"]["since"]
    assert result["advice_id"] == row["id"]


def test_rejected_advice_goes_into_the_prompt(conn: sqlite3.Connection) -> None:
    """A dismissed tip travels into the next tick marked "do not repeat"."""
    first = runner_for(
        envelope(
            [
                {
                    "title": "Switch playwright off",
                    "severity": "info",
                    "detail": "",
                    "action": "remove the server",
                    "evidence": "mcp.servers[0].calls = 176",
                }
            ]
        )
    )
    advisor.advise(conn, DIGEST, runner=first)
    with conn:
        conn.execute("UPDATE advice_items SET status = 'rejected'")

    second = runner_for(envelope([]))
    advisor.advise(conn, DIGEST, runner=second)

    prompt = json.loads(second.prompt)  # type: ignore[attr-defined]
    assert prompt["already_rejected"] == [
        advisor.advice_key("Switch playwright off", "remove the server")
    ]


def test_broken_answer_does_not_break_the_tick(conn: sqlite3.Connection) -> None:
    """An off-schema answer means zero tips, not a failed tick."""
    payload = {"is_error": False, "total_cost_usd": 0.01, "result": "i could not"}

    result = advisor.advise(conn, DIGEST, runner=runner_for(payload))

    assert result["advice"] == []
    assert conn.execute("SELECT COUNT(*) FROM advice").fetchone()[0] == 1, "the tick is stored"


def test_error_envelope_is_raised(conn: sqlite3.Connection) -> None:
    """A CLI error is hidden in an exception, not in an empty list of tips."""

    def run(prompt: str, model: str, budget_usd: float) -> dict:
        return {"is_error": True, "result": "credit balance too low"}

    with pytest.raises(RuntimeError, match="credit balance"):
        advisor.advise(conn, DIGEST, runner=run)
