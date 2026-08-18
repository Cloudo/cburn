"""Carrying tips out: the plan, the confirmation, the write and the way back (task D7).

Nothing here touches the real `~/.claude`: `paths.CLAUDE_DIR` and `paths.DATA_DIR` are
pointed at a temporary directory. A test that wrote into a live config would be exactly
the thing the module exists to prevent.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from cburn import actions, paths
from cburn.db import connect

SETTINGS = {
    "permissions": {"allow": ["Bash(git status)"], "deny": []},
    "hooks": {
        "Stop": [{"hooks": [{"type": "http", "url": "http://127.0.0.1:8788/hook"}]}],
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "./slow.sh"}]},
            {"matcher": "Read", "hooks": [{"type": "command", "command": "./fast.sh"}]},
        ],
    },
    "enabledPlugins": {"playwright@claude-plugins-official": True},
}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake `~/.claude` with a settings file and our own state directory."""
    claude = tmp_path / "claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps(SETTINGS, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(paths, "CLAUDE_DIR", claude)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "state")
    return claude


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "actions.db")


def settings_of(home: Path) -> dict:
    return json.loads((home / "settings.json").read_text(encoding="utf-8"))


def add_session(conn: sqlite3.Connection, session_id: str, **row: object) -> None:
    """A session in the state the status is read from (`metrics.session_status`)."""
    now = datetime.now(UTC)
    values = {
        "id": session_id,
        "started_at": (now - timedelta(minutes=10)).isoformat(),
        "last_at": now.isoformat(),
        "last_record_at": now.isoformat(),
        "last_record_kind": "assistant",
        "last_stop_reason": "end_turn",
        "is_live": 1,
        "busy_since": None,
    } | row
    holes = ",".join(":" + name for name in values)
    with conn:
        conn.execute(
            f"INSERT INTO sessions ({','.join(values)}) VALUES ({holes})",  # noqa: S608
            values,
        )


def add_tool(
    conn: sqlite3.Connection, session_id: str, at: datetime, tool: str, detail: str | None
) -> None:
    """A tool call inside its own turn: the plan reads the step from the last of them."""
    with conn:
        cursor = conn.execute(
            "INSERT INTO turns (message_id, session_id, ts) VALUES (?, ?, ?)",
            (f"msg-{tool}-{at.isoformat()}", session_id, at.isoformat()),
        )
        conn.execute(
            "INSERT INTO tool_calls (turn_id, tool_use_id, tool, detail) VALUES (?, ?, ?, ?)",
            (cursor.lastrowid, f"use-{tool}-{at.isoformat()}", tool, detail),
        )


# --- what comes back from the model -------------------------------------------


def test_an_act_outside_the_list_is_dropped() -> None:
    """The act is executed, so the gate is the narrowest one: only known types get in."""
    assert actions.normalise({"type": "rm_rf", "path": "/"}) is None
    assert actions.normalise({"type": "close_session"}) is None, "no session - no act"
    assert actions.normalise("close everything") is None
    assert actions.normalise({"type": "allow_permission", "rule": "Bash(ls)", "sudo": True}) == {
        "type": "allow_permission",
        "rule": "Bash(ls)",
    }, "an unknown parameter does not travel any further"


# --- a settings patch ----------------------------------------------------------


def test_permission_rule_is_added_and_the_diff_is_real(
    conn: sqlite3.Connection, home: Path
) -> None:
    act = {"type": "allow_permission", "rule": "Bash(npm test:*)"}

    plan = actions.plan(conn, act)
    assert plan.target == str(home / "settings.json")
    assert '+      "Bash(npm test:*)"' in plan.diff
    assert "Bash(git status)" in plan.diff, "the context of the change is visible"
    assert settings_of(home)["permissions"]["allow"] == ["Bash(git status)"], "the plan writes"

    result = actions.apply(conn, act, before_hash=plan.before_hash, item_id=None)
    assert result["status"] == actions.APPLIED
    assert settings_of(home)["permissions"]["allow"][-1] == "Bash(npm test:*)"

    row = conn.execute(
        "SELECT * FROM applied_patches WHERE id = ?", (result["patch_id"],)
    ).fetchone()
    assert row["diff"] == plan.diff, "what was confirmed is what is stored"
    assert Path(row["backup"]).exists(), "there is a copy to roll back from"


def test_a_rule_that_is_already_there_is_not_a_change(conn: sqlite3.Connection, home: Path) -> None:
    with pytest.raises(actions.ActError) as exc:
        actions.plan(conn, {"type": "allow_permission", "rule": "Bash(git status)"})
    assert exc.value.reason == "no_change"


def test_a_foreign_change_between_the_diff_and_the_button_stops_the_write(
    conn: sqlite3.Connection, home: Path
) -> None:
    """Claude Code rewrites `settings.json` itself - a stale plan must not wipe that out."""
    act = {"type": "allow_permission", "rule": "Bash(npm test:*)"}
    plan = actions.plan(conn, act)

    changed = SETTINGS | {"permissions": {"allow": ["Bash(git status)", "Read(//tmp/**)"]}}
    (home / "settings.json").write_text(json.dumps(changed, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(actions.ActError) as exc:
        actions.apply(conn, act, before_hash=plan.before_hash, item_id=None)
    assert exc.value.reason == "stale"
    assert "Read(//tmp/**)" in (home / "settings.json").read_text(), "the foreign rule survived"


def test_a_hook_goes_with_the_whole_event_or_by_the_matcher(
    conn: sqlite3.Connection, home: Path
) -> None:
    actions.apply(
        conn,
        {"type": "disable_hook", "event": "Stop"},
        before_hash=actions.plan(conn, {"type": "disable_hook", "event": "Stop"}).before_hash,
        item_id=None,
    )
    assert "Stop" not in settings_of(home)["hooks"]

    act = {"type": "disable_hook", "event": "PreToolUse", "matcher": "Bash"}
    actions.apply(conn, act, before_hash=actions.plan(conn, act).before_hash, item_id=None)
    kept = settings_of(home)["hooks"]["PreToolUse"]
    assert [group["matcher"] for group in kept] == ["Read"], "only the named one goes"

    with pytest.raises(actions.ActError) as exc:
        actions.plan(conn, {"type": "disable_hook", "event": "SessionStart"})
    assert exc.value.reason == "not_found"


def test_a_plugin_is_switched_off_by_the_short_name(conn: sqlite3.Connection, home: Path) -> None:
    """In the digest a plugin is `playwright`, in the file `playwright@source`."""
    act = {"type": "disable_plugin", "plugin": "playwright"}

    actions.apply(conn, act, before_hash=actions.plan(conn, act).before_hash, item_id=None)

    assert settings_of(home)["enabledPlugins"] == {"playwright@claude-plugins-official": False}


def test_the_project_scope_writes_the_personal_file(
    conn: sqlite3.Connection, home: Path, tmp_path: Path
) -> None:
    """`settings.json` of a repository is committed - a local decision goes to `.local`."""
    root = tmp_path / "repo"
    root.mkdir()
    with conn:
        conn.execute(
            "INSERT INTO projects (slug, root_path, display_name) VALUES (?, ?, ?)",
            ("repo", str(root), "repo"),
        )
    act = {
        "type": "allow_permission",
        "rule": "Bash(pytest:*)",
        "scope": "project",
        "project": "repo",
    }

    plan = actions.plan(conn, act)
    assert plan.target == str(root / ".claude" / "settings.local.json")

    result = actions.apply(conn, act, before_hash=plan.before_hash, item_id=None)
    assert json.loads(Path(plan.target).read_text())["permissions"]["allow"] == ["Bash(pytest:*)"]

    # The file did not exist before us, so the way back is to remove it, not to restore.
    actions.rollback(conn, result["patch_id"])
    assert not Path(plan.target).exists()


def test_an_unknown_project_does_not_reach_the_filesystem(
    conn: sqlite3.Connection, home: Path
) -> None:
    with pytest.raises(actions.ActError) as exc:
        actions.plan(conn, {"type": "allow_permission", "rule": "Bash(ls)", "scope": "project"})
    assert exc.value.reason == "no_project"


# --- the way back --------------------------------------------------------------


def test_rollback_restores_the_file(conn: sqlite3.Connection, home: Path) -> None:
    before = (home / "settings.json").read_text()
    act = {"type": "allow_permission", "rule": "Bash(npm test:*)"}
    result = actions.apply(conn, act, before_hash=actions.plan(conn, act).before_hash, item_id=None)

    actions.rollback(conn, result["patch_id"])

    assert (home / "settings.json").read_text() == before
    row = conn.execute("SELECT status FROM applied_patches WHERE id = ?", (result["patch_id"],))
    assert row.fetchone()["status"] == actions.ROLLED_BACK


def test_rollback_does_not_throw_away_a_later_change(conn: sqlite3.Connection, home: Path) -> None:
    """After us the file was edited by a human or by Claude Code: the copy is no longer valid."""
    act = {"type": "allow_permission", "rule": "Bash(npm test:*)"}
    result = actions.apply(conn, act, before_hash=actions.plan(conn, act).before_hash, item_id=None)
    later = settings_of(home)
    later["permissions"]["allow"].append("Read(//tmp/**)")
    (home / "settings.json").write_text(json.dumps(later, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(actions.ActError) as exc:
        actions.rollback(conn, result["patch_id"])

    assert exc.value.reason == "changed_since"
    assert "Read(//tmp/**)" in (home / "settings.json").read_text()


# --- closing a session ---------------------------------------------------------


def test_a_working_session_is_closed_in_the_pause_not_at_once(
    conn: sqlite3.Connection, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    add_session(
        conn,
        "b2ae5a8a-1111-2222-3333-444455556666",
        last_stop_reason="tool_use",
        last_record_at=now.isoformat(),
        busy_since=now.isoformat(),  # a child started after the request: a tool is running
    )
    killed: list[int] = []
    monkeypatch.setattr(actions, "process_for_session", lambda _id: SimpleNamespace(pid=777))
    monkeypatch.setattr(actions, "terminate", lambda pid: killed.append(pid) or True)

    act = {"type": "close_session", "session_id": "b2ae5a8a"}
    plan = actions.plan(conn, act)
    assert "sigterm" in plan.notes, "the honest warning: SessionEnd hooks will not run"
    result = actions.apply(conn, act, before_hash=plan.before_hash, item_id=None)
    assert result["status"] == actions.PENDING

    live = {"b2ae5a8a-1111-2222-3333-444455556666": now}
    assert actions.run_pending(conn, live, now=now) == 0
    assert not killed, "a step is running - we wait"

    # The tool has answered: the session is between steps now.
    with conn:
        conn.execute("UPDATE sessions SET last_stop_reason = 'end_turn', busy_since = NULL")
    assert actions.run_pending(conn, live, now=now) == 1
    assert killed == [777]
    row = conn.execute("SELECT status, note FROM applied_patches").fetchone()
    assert (row["status"], row["note"]) == (actions.APPLIED, "terminated")


def test_the_plan_names_the_step_that_would_be_interrupted(
    conn: sqlite3.Connection, home: Path
) -> None:
    """A status word is not an answer: the plan says which task and which tool."""
    now = datetime.now(UTC)
    session = "d4d4d4d4-1111-2222-3333-444455556666"
    add_session(
        conn,
        session,
        title="the parser tail",
        last_prompt="reindex the transcripts and check the offsets",
        last_stop_reason="tool_use",
        last_record_at=now.isoformat(),
        busy_since=now.isoformat(),
    )
    add_tool(conn, session, now - timedelta(minutes=2), "Read", None)
    add_tool(conn, session, now, "Bash", "pytest")

    details = actions.plan(conn, {"type": "close_session", "session_id": "d4d4d4d4"}).details

    assert details["title"] == "the parser tail"
    assert details["prompt"] == "reindex the transcripts and check the offsets"
    assert details["tool"] == {"name": "Bash", "detail": "pytest"}, "the last tool, not the first"

    # The answer has come: the turn has moved on and the old tool is no longer the step.
    with conn:
        conn.execute("UPDATE sessions SET last_stop_reason = 'end_turn'")
    after = actions.plan(conn, {"type": "close_session", "session_id": "d4d4d4d4"}).details
    assert after["tool"] is None


def test_a_pending_close_can_be_cancelled(conn: sqlite3.Connection, home: Path) -> None:
    add_session(conn, "aaaa1111-1111-2222-3333-444455556666")
    act = {"type": "close_session", "session_id": "aaaa1111"}
    result = actions.apply(conn, act, before_hash="", item_id=None)

    actions.rollback(conn, result["patch_id"])

    assert actions.run_pending(conn, {}, now=datetime.now(UTC)) == 0


def test_a_session_that_has_already_gone_is_not_signalled(
    conn: sqlite3.Connection, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_session(conn, "cccc2222-1111-2222-3333-444455556666", is_live=0)
    act = {"type": "close_session", "session_id": "cccc2222"}
    actions.apply(conn, act, before_hash="", item_id=None)
    monkeypatch.setattr(actions, "terminate", lambda pid: pytest.fail("nothing to terminate"))

    assert actions.run_pending(conn, {}, now=datetime.now(UTC)) == 1

    row = conn.execute("SELECT status, note FROM applied_patches").fetchone()
    assert (row["status"], row["note"]) == (actions.APPLIED, "already_gone")
    assert conn.execute("SELECT hidden FROM sessions").fetchone()["hidden"] == 1
