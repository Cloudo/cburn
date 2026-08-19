"""HTTP and WebSocket tests (task A5)."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cburn import config, paths
from cburn import metrics as metrics_module
from cburn.api.server import create_app
from cburn.collector.indexer import ingest_tree
from cburn.db import connect
from cburn.metrics import TS_FORMAT


def assistant(
    message_id: str,
    *,
    session: str = "s1",
    uuid: str = "u1",
    ts: datetime | None = None,
    output: int = 100,
    cache_read: int = 1000,
    content: list[dict] | None = None,
    stop_reason: str = "end_turn",
) -> str:
    moment = ts or datetime.now(UTC)
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "sessionId": session,
            "timestamp": moment.astimezone(UTC).strftime(TS_FORMAT),
            "cwd": "/Users/x/project",
            "message": {
                "id": message_id,
                "model": "claude-opus-5",
                "stop_reason": stop_reason,
                "content": content
                or [
                    {
                        "type": "tool_use",
                        "id": f"t-{message_id}",
                        "name": "Bash",
                        "input": {"command": "ls -la"},
                    }
                ],
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": output,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": 50,
                        "ephemeral_5m_input_tokens": 0,
                    },
                },
            },
        }
    )


def prompt(text: str, *, session: str = "s1", ts: datetime | None = None) -> str:
    return json.dumps(
        {
            "type": "user",
            "uuid": f"p-{session}-{text[:6]}",
            "sessionId": session,
            "timestamp": (ts or datetime.now(UTC)).astimezone(UTC).strftime(TS_FORMAT),
            "message": {"content": text},
        }
    )


@pytest.fixture
def transcripts(tmp_path: Path) -> Path:
    root = tmp_path / "projects" / "project"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "api.db"


def seed(transcripts: Path, db_path: Path, lines: list[str], name: str = "s1.jsonl") -> None:
    (transcripts / name).write_text("".join(line + "\n" for line in lines))
    conn = connect(db_path)
    ingest_tree(conn, transcripts.parent)
    conn.close()


class StubLimits:
    """In tests the limits touch neither the keychain nor the network."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {
            "source": "none",
            "fetched_at": None,
            "plan": None,
            "tier": None,
            "limits": [],
            "error": None,
        }
        self.refreshes = 0

    def current(self, now: float | None = None, *, force: bool = False) -> dict:
        return self.payload

    def refresh(self, now: float | None = None) -> dict:
        self.refreshes += 1
        return self.payload


def client(
    db_path: Path,
    transcripts: Path,
    *,
    watch: bool = False,
    limits: object | None = None,
    liveness: Callable[[], dict[str, datetime | None] | None] = lambda: None,
    advisor_run: object | None = None,
) -> TestClient:
    """The test application. By default liveness is "unknown", and the advisor
    fails when called: tests must run neither `claude agents --json`
    nor `claude -p` - the second one costs money as well."""

    def no_advisor(*args: object, **kwargs: object) -> dict:
        raise AssertionError("a test must not call the real claude -p")

    app = create_app(
        db_path=db_path,
        projects_dir=transcripts.parent,
        watch=watch,
        limits=limits or StubLimits(),  # type: ignore[arg-type]
        liveness=liveness,
        advisor_run=advisor_run or no_advisor,
    )
    return TestClient(app)


# --- overview -----------------------------------------------------------------


def test_overview_counts_recent_turns(transcripts: Path, db_path: Path) -> None:
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            prompt("first question"),
            assistant("msg_1", ts=now - timedelta(seconds=20), output=100),
            assistant("msg_2", uuid="u2", ts=now - timedelta(seconds=40), output=200),
            assistant("msg_old", uuid="u3", ts=now - timedelta(hours=5), output=999),
        ],
    )
    with client(db_path, transcripts) as api:
        data = api.get("/api/overview").json()

    assert data["totals"]["turns"] == 3
    assert data["burn"]["10s"]["turns"] == 0  # both turns are older than ten seconds
    assert data["burn"]["1m"]["turns"] == 2  # the five-hour-old turn is out of the window
    assert data["burn"]["1m"]["tokens_per_min"] == pytest.approx(2 * 2 + 300 + 2000 + 100)
    assert data["burn"]["60m"]["turns"] == 2
    assert data["today"]["output_tokens"] >= 300
    assert data["live_sessions"], "the live session is not shown"
    assert data["live_sessions"][0]["id"] == "s1"
    assert data["top_sessions"][0]["id"] == "s1"


def test_overview_on_empty_db(db_path: Path, transcripts: Path) -> None:
    with client(db_path, transcripts) as api:
        data = api.get("/api/overview").json()
    assert data["totals"]["turns"] == 0
    assert data["burn"]["1m"]["tokens_per_min"] == 0
    assert data["live_sessions"] == []


def test_burn_rate_is_per_minute(transcripts: Path, db_path: Path) -> None:
    """The 5-minute window divides by 5 - otherwise the needle lies fivefold."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [assistant("msg_1", ts=now - timedelta(minutes=3), output=500, cache_read=0)],
    )
    with client(db_path, transcripts) as api:
        burn = api.get("/api/overview").json()["burn"]

    assert burn["1m"]["turns"] == 0
    assert burn["5m"]["output_per_min"] == pytest.approx(100)
    assert burn["60m"]["output_per_min"] == pytest.approx(500 / 60)
    assert burn["5m"]["window_seconds"] == 300


def test_live_burn_decays_with_age(transcripts: Path, db_path: Path) -> None:
    """The live needle weighs a turn by its age: half-worth after ~21 s, not a cliff."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [assistant("msg_1", ts=now - timedelta(seconds=30), output=500, cache_read=0)],
    )
    with client(db_path, transcripts) as api:
        burn = api.get("/api/overview").json()["burn"]

    live = burn["live"]
    # tokens = 2 input + 500 output + 50 cache write; weight = exp(-30/30) / half a minute
    weight = 2 * math.exp(-1)
    assert live["turns"] == 1
    assert live["tokens_per_min"] == pytest.approx(552 * weight, rel=0.05)
    assert live["output_per_min"] == pytest.approx(500 * weight, rel=0.05)
    # the legend is weighted the same way and sums to the needle
    assert live["usage"]["tokens"] == pytest.approx(live["tokens_per_min"])
    assert burn["5s"]["window_seconds"] == 5


def test_live_burn_forgets_old_turns(transcripts: Path, db_path: Path) -> None:
    """Beyond the decay cutoff a turn no longer moves the live needle at all."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [assistant("msg_1", ts=now - timedelta(minutes=4), output=500)],
    )
    with client(db_path, transcripts) as api:
        live = api.get("/api/overview").json()["burn"]["live"]

    assert live["turns"] == 0
    assert live["tokens_per_min"] == 0


# --- sessions -----------------------------------------------------------------


def test_sessions_list(transcripts: Path, db_path: Path) -> None:
    seed(transcripts, db_path, [prompt("question"), assistant("msg_1")])
    with client(db_path, transcripts) as api:
        sessions = api.get("/api/sessions").json()["sessions"]
    assert [row["id"] for row in sessions] == ["s1"]
    assert sessions[0]["first_prompt"] == "question"
    # The project name is the last segment of the working path (cwd), not the name of
    # the transcript directory: a slug like `-Users-x-project` tells a human nothing.
    assert sessions[0]["project"] == "project"


def test_session_details(transcripts: Path, db_path: Path) -> None:
    seed(transcripts, db_path, [prompt("question"), assistant("msg_1", output=42)])
    with client(db_path, transcripts) as api:
        data = api.get("/api/sessions/s1").json()

    assert data["session"]["output_tokens"] == 42
    assert data["session"]["cache_write"] == 50
    assert data["models"] == [{"model": "claude-opus-5", "turns": 1, "output_tokens": 42}]
    assert data["tools"] == [{"tool": "Bash", "calls": 1}]


def test_unknown_session_is_404(db_path: Path, transcripts: Path) -> None:
    with client(db_path, transcripts) as api:
        assert api.get("/api/sessions/no-such").status_code == 404


def test_health(db_path: Path, transcripts: Path) -> None:
    with client(db_path, transcripts) as api:
        assert api.get("/api/health").json()["ok"] is True


def test_root_reports_missing_frontend(
    db_path: Path, transcripts: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While the frontend is not built, the root hints at how to do it."""
    from cburn.api import server

    monkeypatch.setattr(server, "WEB_DIST", tmp_path / "no-build")
    with client(db_path, transcripts) as api:
        body = api.get("/").json()
    assert "/api/overview" in body["api"]


# --- WebSocket ---------------------------------------------------------------


def test_ws_sends_overview_on_connect(transcripts: Path, db_path: Path) -> None:
    seed(transcripts, db_path, [assistant("msg_1")])
    with client(db_path, transcripts) as api, api.websocket_connect("/ws") as socket:
        message = socket.receive_json()
    assert message["type"] == "overview"
    assert message["data"]["totals"]["turns"] == 1


def test_ws_pushes_on_new_turn(transcripts: Path, db_path: Path) -> None:
    """A line appended to a transcript leads to a push within a second."""
    seed(transcripts, db_path, [assistant("msg_1")])
    with (
        client(db_path, transcripts, watch=True) as api,
        api.websocket_connect("/ws") as socket,
    ):
        assert socket.receive_json()["data"]["totals"]["turns"] == 1

        started = time.monotonic()
        with (transcripts / "s1.jsonl").open("a") as fh:
            fh.write(assistant("msg_2", uuid="u2", output=7) + "\n")

        message = socket.receive_json()
        elapsed = time.monotonic() - started

    assert message["type"] == "overview"
    assert message["data"]["totals"]["turns"] == 2
    assert elapsed < 1.0, f"the push arrived in {elapsed:.2f} s - milestone A criterion failed"


def test_ws_serves_two_clients(transcripts: Path, db_path: Path) -> None:
    seed(transcripts, db_path, [assistant("msg_1")])
    with (
        client(db_path, transcripts, watch=True) as api,
        api.websocket_connect("/ws") as first,
        api.websocket_connect("/ws") as second,
    ):
        first.receive_json()
        second.receive_json()
        with (transcripts / "s1.jsonl").open("a") as fh:
            fh.write(assistant("msg_2", uuid="u2") + "\n")
        assert first.receive_json()["data"]["totals"]["turns"] == 2
        assert second.receive_json()["data"]["totals"]["turns"] == 2


def test_watcher_stops_with_app(transcripts: Path, db_path: Path) -> None:
    """After the application stops, no background thread is left behind."""
    import threading

    before = {thread.name for thread in threading.enumerate()}
    with client(db_path, transcripts, watch=True) as api:
        api.get("/api/health")

    # We wait for the thread to finish rather than sleeping a fixed fraction of a second:
    # on a loaded machine the scheduler does not give it a slot right away, and a rigid
    # pause turns the test into a lottery.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        alive = {thread.name for thread in threading.enumerate()} - before
        if "cburn-watcher" not in alive:
            break
        time.sleep(0.05)

    after = {thread.name for thread in threading.enumerate()}
    assert "cburn-watcher" not in after - before


def test_built_frontend_is_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The built frontend is served as statics from the same port as the API."""
    from cburn.api import server

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>cburn</title>")
    monkeypatch.setattr(server, "WEB_DIST", dist)

    app = server.create_app(db_path=tmp_path / "api.db", watch=False)
    with TestClient(app) as api:
        page = api.get("/")
        assert page.status_code == 200
        assert "cburn" in page.text
        assert api.get("/api/health").json()["ok"] is True  # the API is not shadowed by statics


def test_frontend_cache_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The browser revalidates the shell every time, hashed assets are cached forever.

    Without this a rebuilt frontend loads inside the old shell from the browser cache.
    """
    from cburn.api import server

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>cburn</title>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    monkeypatch.setattr(server, "WEB_DIST", dist)

    app = server.create_app(db_path=tmp_path / "api.db", watch=False)
    with TestClient(app) as api:
        assert api.get("/").headers["cache-control"] == "no-cache"
        assert "immutable" in api.get("/assets/index-abc123.js").headers["cache-control"]


# --- the chart recorder and live readings --------------------------------------


def test_series_has_bucket_per_step(transcripts: Path, db_path: Path) -> None:
    """The recorder tape is a continuous grid of buckets, empty ones included."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            # Both turns share one timestamp: otherwise they land in neighbouring
            # buckets when the measurement falls on a step boundary.
            assistant("msg_1", ts=now - timedelta(seconds=8), output=100, cache_read=0),
            assistant("msg_2", uuid="u2", ts=now - timedelta(seconds=8), output=50, cache_read=0),
            assistant("msg_3", uuid="u3", ts=now - timedelta(minutes=2), output=10, cache_read=0),
        ],
    )
    with client(db_path, transcripts) as api:
        data = api.get("/api/overview").json()

    series = data["series"]
    assert data["series_bucket_seconds"] == 2
    assert len(series) >= 5 * 30  # five minutes at two seconds each
    assert sum(bucket["turns"] for bucket in series) == 3
    assert sum(bucket["output_tokens"] for bucket in series) == 160
    assert any(bucket["turns"] == 0 for bucket in series), "empty buckets are not filled"
    # Neighbouring turns land in one bucket: the step is exactly 2 seconds, not one.
    busiest = max(series, key=lambda bucket: bucket["turns"])
    assert busiest["turns"] == 2


def test_pending_session_is_reported(transcripts: Path, db_path: Path) -> None:
    """A prompt without an answer is the sign that a request is running right now."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", ts=now - timedelta(seconds=30)),
            prompt("new question", ts=now - timedelta(seconds=3)),
        ],
    )
    with client(db_path, transcripts) as api:
        assert api.get("/api/overview").json()["pending_sessions"] == ["s1"]


def test_answered_session_is_not_pending(transcripts: Path, db_path: Path) -> None:
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            prompt("question", ts=now - timedelta(seconds=20)),
            assistant("msg_1", ts=now - timedelta(seconds=5)),
        ],
    )
    with client(db_path, transcripts) as api:
        assert api.get("/api/overview").json()["pending_sessions"] == []


def test_ws_pushes_without_new_turns(transcripts: Path, db_path: Path) -> None:
    """The ticker sends the overview in silence too: burn rate windows slide on their own."""
    from cburn.api import server

    seed(transcripts, db_path, [assistant("msg_1")])
    with (
        client(db_path, transcripts, watch=False) as api,
        api.websocket_connect("/ws") as socket,
    ):
        socket.receive_json()  # the frame on connect
        started = time.monotonic()
        message = socket.receive_json()  # the frame from the ticker, nobody touched the files
        elapsed = time.monotonic() - started

    assert message["type"] == "overview"
    assert elapsed < server.TICK_SECONDS * 2


def test_ten_second_window_reacts_immediately(transcripts: Path, db_path: Path) -> None:
    """A short window shows what happens right now, not the average."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [assistant("msg_1", ts=now - timedelta(seconds=3), output=60, cache_read=0)],
    )
    with client(db_path, transcripts) as api:
        burn = api.get("/api/overview").json()["burn"]

    assert burn["10s"]["window_seconds"] == 10
    # Six seconds of work inside a ten-second window is 360 tokens per minute.
    assert burn["10s"]["output_per_min"] == pytest.approx(360)
    assert burn["1m"]["output_per_min"] == pytest.approx(60)


def test_burn_window_carries_its_own_usage(transcripts: Path, db_path: Path) -> None:
    """The per-part breakdown is available for every window, not only for today."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", ts=now - timedelta(seconds=3), output=60, cache_read=900),
            assistant("msg_2", uuid="u2", ts=now - timedelta(minutes=3), output=10, cache_read=100),
        ],
    )
    with client(db_path, transcripts) as api:
        burn = api.get("/api/overview").json()["burn"]

    assert burn["10s"]["usage"]["cache_read"] == 900  # only the fresh turn
    assert burn["10s"]["usage"]["output_tokens"] == 60
    assert burn["5m"]["usage"]["cache_read"] == 1000  # both turns
    assert burn["5m"]["usage"]["cache_write"] == 100  # 50 per turn


# --- closing a session ---------------------------------------------------------


def test_hide_removes_session_from_dashboard(transcripts: Path, db_path: Path) -> None:
    seed(transcripts, db_path, [prompt("question"), assistant("msg_1")])
    with client(db_path, transcripts) as api:
        assert len(api.get("/api/overview").json()["live_sessions"]) == 1

        assert api.post("/api/sessions/s1/hide").json() == {"session_id": "s1", "hidden": True}
        assert api.get("/api/overview").json()["live_sessions"] == []
        assert api.get("/api/sessions").json()["sessions"] == []

        api.post("/api/sessions/s1/hide", params={"hidden": False})
        assert len(api.get("/api/overview").json()["live_sessions"]) == 1


def test_hide_unknown_session_is_404(db_path: Path, transcripts: Path) -> None:
    with client(db_path, transcripts) as api:
        assert api.post("/api/sessions/no-such/hide").status_code == 404


def test_close_terminates_the_session_process(
    transcripts: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process is taken by sessionId from the Claude Code list and gets a SIGTERM."""
    from cburn.api import server
    from cburn.processes import ClaudeSession

    seed(transcripts, db_path, [assistant("msg_1")])
    killed: list[int] = []
    monkeypatch.setattr(
        server, "process_for_session", lambda sid: ClaudeSession(pid=4242, session_id=sid)
    )
    monkeypatch.setattr(server, "terminate", lambda pid: killed.append(pid) is None)

    with client(db_path, transcripts) as api:
        result = api.post("/api/sessions/s1/close").json()
        assert result["stopped"] is True
        assert result["pid"] == 4242
        assert killed == [4242]
        assert api.get("/api/overview").json()["live_sessions"] == []


def test_close_of_finished_session_only_hides(
    transcripts: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session is no longer among the running ones - we just remove the card."""
    from cburn.api import server

    seed(transcripts, db_path, [assistant("msg_1")])
    monkeypatch.setattr(server, "process_for_session", lambda sid: None)

    with client(db_path, transcripts) as api:
        result = api.post("/api/sessions/s1/close").json()
        assert result["stopped"] is False
        assert result["pid"] is None
        assert "the process is gone" in result["note"]
        assert api.get("/api/overview").json()["live_sessions"] == []


def test_live_sessions_are_sorted_by_activity(transcripts: Path, db_path: Path) -> None:
    """The freshest session on top; how many to show is the dashboard's call."""
    now = datetime.now(UTC)
    lines = [
        assistant(
            f"msg_{index}",
            session=f"s{index}",
            uuid=f"u{index}",
            ts=now - timedelta(seconds=index * 5),
        )
        for index in range(7)
    ]
    seed(transcripts, db_path, lines)

    with client(db_path, transcripts) as api:
        data = api.get("/api/overview").json()

    live = data["live_sessions"]
    assert [row["id"] for row in live] == [f"s{index}" for index in range(7)]
    assert data["live_limit"] == 5


def test_session_statuses(transcripts: Path, db_path: Path) -> None:
    """The status answers the question of whom the session is waiting for."""
    now = datetime.now(UTC)
    tool_use = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]
    seed(
        transcripts,
        db_path,
        [
            # The model works: the last record is a prompt without an answer.
            assistant("msg_a", session="working", ts=now - timedelta(seconds=40)),
            prompt("keep counting", session="working", ts=now - timedelta(seconds=20)),
            # The model answered and waits for the human.
            assistant("msg_b", session="answered", uuid="u2", ts=now - timedelta(seconds=30)),
            # A tool was requested, there is no result - a permission hangs.
            assistant(
                "msg_c",
                session="permission",
                uuid="u3",
                ts=now - timedelta(seconds=60),
                content=tool_use,
                stop_reason="tool_use",
            ),
            # Silence for longer than two minutes.
            assistant("msg_d", session="idle", uuid="u4", ts=now - timedelta(minutes=20)),
        ],
    )
    with client(db_path, transcripts) as api:
        data = api.get("/api/overview").json()

    statuses = {row["id"]: row["status"] for row in data["live_sessions"]}
    assert statuses == {
        "working": "working",
        "answered": "answered",
        "permission": "permission",
        "idle": "idle",
    }
    assert data["pending_sessions"] == ["working"]


def test_long_tool_is_working_not_permission(transcripts: Path, db_path: Path) -> None:
    """A long tool is not a hanging permission: the process has a fresh child.

    In the transcript both cases look the same (a tool request without an answer),
    only the process tells them apart: running the tests spawns a child, while on
    an "allow?" question the process idles.
    """
    now = datetime.now(UTC)
    tool_use = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]
    asked = now - timedelta(seconds=90)
    lines = [
        assistant(
            "msg_a",
            session=session,
            uuid=f"u-{session}",
            ts=asked,
            content=tool_use,
            stop_reason="tool_use",
        )
        for session in ("running", "asking", "old-child")
    ]
    seed(transcripts, db_path, lines)

    def liveness() -> dict[str, datetime | None]:
        return {
            "running": asked + timedelta(seconds=1),  # the child started on the request
            "asking": None,  # no children - the process waits for the human
            "old-child": asked - timedelta(hours=1),  # an MCP server, does not count
        }

    with client(db_path, transcripts, liveness=liveness) as api:
        data = api.get("/api/overview").json()

    statuses = {row["id"]: row["status"] for row in data["live_sessions"]}
    assert statuses == {
        "running": "working",
        "asking": "permission",
        "old-child": "permission",
    }


def test_finished_session_leaves_idle(transcripts: Path, db_path: Path) -> None:
    """A quiet session without a process is "finished", not "idle" (B4)."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_a", session="alive", ts=now - timedelta(minutes=20)),
            assistant("msg_b", session="dead", uuid="u2", ts=now - timedelta(minutes=20)),
        ],
    )

    with client(db_path, transcripts, liveness=lambda: {"alive": None}) as api:
        data = api.get("/api/overview").json()

    statuses = {row["id"]: row["status"] for row in data["live_sessions"]}
    assert statuses == {"alive": "idle", "dead": "done"}


def test_unknown_liveness_keeps_idle(transcripts: Path, db_path: Path) -> None:
    """A silent `claude` is no reason to declare every session finished (B4)."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [assistant("msg_a", session="quiet", ts=now - timedelta(minutes=20))],
    )

    with client(db_path, transcripts, liveness=lambda: None) as api:
        data = api.get("/api/overview").json()

    assert [row["status"] for row in data["live_sessions"]] == ["idle"]


# --- the "Sessions" screen (task C1) -------------------------------------------


def test_sessions_page_filters_and_sparkline(transcripts: Path, db_path: Path) -> None:
    """The list is filtered by project and status and carries a spend sparkline."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", session="live", ts=now - timedelta(seconds=30)),
            assistant("msg_2", session="old", uuid="u2", ts=now - timedelta(hours=3)),
        ],
    )

    with client(db_path, transcripts, liveness=lambda: {"live": None}) as api:
        page = api.get("/api/sessions").json()
        only_done = api.get("/api/sessions?status=done").json()
        nothing = api.get("/api/sessions?project=nosuch").json()

    assert {row["id"] for row in page["sessions"]} == {"live", "old"}
    assert [row["id"] for row in only_done["sessions"]] == ["old"]
    assert nothing["sessions"] == []
    assert page["projects"][0]["sessions"] == 2
    spark = next(row["spark"] for row in page["sessions"] if row["id"] == "live")
    assert len(spark) == 24 and sum(spark) > 0


def test_sessions_page_period_cuts_old(transcripts: Path, db_path: Path) -> None:
    """The period cuts off the old: `24h` does not show yesterday's session."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", session="fresh", ts=now - timedelta(minutes=5)),
            assistant("msg_2", session="yesterday", uuid="u2", ts=now - timedelta(days=2)),
        ],
    )

    with client(db_path, transcripts) as api:
        recent = api.get("/api/sessions?period=24h").json()["sessions"]
        everything = api.get("/api/sessions?period=all").json()["sessions"]

    assert [row["id"] for row in recent] == ["fresh"]
    assert len(everything) == 2


def test_sessions_page_marks_resume_chain(transcripts: Path, db_path: Path) -> None:
    """A continuation shows its parent, the parent shows a continuation counter."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [assistant("msg_1", session="base", ts=now - timedelta(minutes=30))],
    )
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", session="resumed", ts=now - timedelta(minutes=30)),
            assistant("msg_2", session="resumed", uuid="u2", ts=now - timedelta(minutes=5)),
        ],
        "second.jsonl",
    )

    with client(db_path, transcripts) as api:
        rows = {row["id"]: row for row in api.get("/api/sessions").json()["sessions"]}

    assert rows["resumed"]["parent_session_id"] == "base"
    assert rows["base"]["children"] == 1


def test_session_details_carry_turns_and_marks(transcripts: Path, db_path: Path) -> None:
    """The "Session" screen: turns in order, idle ones marked, milestones collected (C2)."""
    now = datetime.now(UTC)
    compacted = json.dumps(
        {
            "type": "user",
            "uuid": "compact-1",
            "sessionId": "s1",
            "timestamp": (now - timedelta(minutes=8)).isoformat().replace("+00:00", "Z"),
            "isCompactSummary": True,
            "message": {"role": "user", "content": "a retelling of the conversation"},
        }
    )
    seed(
        transcripts,
        db_path,
        [
            # An ordinary turn and an idle one: a short answer on a large context.
            assistant("msg_1", ts=now - timedelta(minutes=10), output=500, cache_read=60_000),
            compacted,
            assistant(
                "msg_2", uuid="u2", ts=now - timedelta(minutes=5), output=5, cache_read=60_000
            ),
        ],
    )

    with client(db_path, transcripts) as api:
        data = api.get("/api/sessions/s1").json()

    turns = data["turns"]
    assert [turn["message_id"] for turn in turns] == ["msg_1", "msg_2"]
    assert [bool(turn["is_idle"]) for turn in turns] == [False, True]
    assert [event["kind"] for event in data["events"]] == ["compact"]


# --- the "Settings" screen (task C3) -------------------------------------------


def test_config_is_read_and_written(
    transcripts: Path, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings are read, written into the file and apply prices right away."""
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(paths, "CONFIG_PATH", config_path)
    now = datetime.now(UTC)
    seed(
        transcripts, db_path, [assistant("msg_1", ts=now - timedelta(minutes=1), output=1_000_000)]
    )

    with client(db_path, transcripts) as api:
        current = api.get("/api/config").json()["config"]
        current["thresholds"]["context_warn"] = 90_000
        current["prices"] = {
            "claude-opus-5": {
                "input": 5.0,
                "output": 25.0,
                "cache_write_5m": 6.25,
                "cache_write_1h": 10.0,
                "cache_read": 0.5,
            }
        }
        saved = api.put("/api/config", json={"config": current})
        again = api.get("/api/config").json()["config"]

    assert saved.status_code == 200
    assert again["thresholds"]["context_warn"] == 90_000
    assert config_path.exists(), "the config must land in the file, not stay in memory"
    conn = connect(db_path, apply_schema=False)
    try:
        cost = conn.execute("SELECT cost_usd FROM turns WHERE message_id = 'msg_1'").fetchone()[0]
    finally:
        conn.close()
    assert cost > 0, "prices must apply right away, without a reindex"


def test_config_rejects_broken_values(
    transcripts: Path, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bad values never reach the file, they are explained to the human instead."""
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(paths, "CONFIG_PATH", config_path)
    seed(transcripts, db_path, [assistant("msg_1")])

    with client(db_path, transcripts) as api:
        current = api.get("/api/config").json()["config"]
        current["thresholds"]["context_warn"] = 200_000  # yellow later than red
        current["telegram"]["daily_summary_at"] = "in the evening"
        current["analyzer"]["language"] = "klingon"
        response = api.put("/api/config", json={"config": current})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "yellow zone" in detail and "daily_summary_at" in detail
    assert "analyzer.language" in detail
    assert not config_path.exists()


# --- the interface language for the native surfaces ----------------------------


def test_ui_language_is_mirrored_for_the_tray(
    transcripts: Path, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The browser's choice lands in a file: the tray has no access to localStorage."""
    state_path = tmp_path / "ui.json"
    monkeypatch.setattr(paths, "UI_STATE_PATH", state_path)
    seed(transcripts, db_path, [assistant("msg_1")])

    with client(db_path, transcripts) as api:
        assert api.get("/api/ui").json() == {"lang": None}, "nobody has chosen yet"
        saved = api.post("/api/ui/lang?lang=ru")
        assert saved.status_code == 200
        assert api.get("/api/ui").json() == {"lang": "ru"}

    assert json.loads(state_path.read_text()) == {"lang": "ru"}


def test_ui_language_rejects_an_unknown_one(
    transcripts: Path, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the languages of the dictionary: the tray has no third one."""
    state_path = tmp_path / "ui.json"
    monkeypatch.setattr(paths, "UI_STATE_PATH", state_path)
    seed(transcripts, db_path, [assistant("msg_1")])

    with client(db_path, transcripts) as api:
        response = api.post("/api/ui/lang?lang=klingon")

    assert response.status_code == 400
    assert not state_path.exists(), "a bad value must not reach the file"


# --- the "Advice" screen (task D6) ---------------------------------------------


def advice_run(conn, *, kind: str = "hourly", cost: float = 0.08) -> int:
    """An analysis with two tips straight in the database: the tick itself is not needed here."""
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO advice (ts, kind, digest_json, model, cost_usd, max_severity)
            VALUES (?, ?, '{}', 'claude-haiku-4-5', ?, 'warn')
            """,
            (datetime.now(UTC).isoformat(), kind, cost),
        )
        advice_id = cursor.lastrowid
        conn.executemany(
            """
            INSERT INTO advice_items (advice_id, key, title, severity, detail, action, evidence)
            VALUES (?, ?, ?, ?, '', '', ?)
            """,
            [
                (advice_id, "k1", "Close the work line", "warn", "chains[0].sessions = 19"),
                (advice_id, "k2", "Move reading to haiku", "info", "mechanical_opus = 212"),
            ],
        )
    return int(advice_id or 0)


def test_advice_history_is_served(transcripts: Path, db_path: Path) -> None:
    """The screen receives analyses with nested tips."""
    conn = connect(db_path)
    advice_run(conn)
    conn.close()

    with client(db_path, transcripts) as api:
        runs = api.get("/api/advice").json()["runs"]

    assert len(runs) == 1
    assert runs[0]["cost_usd"] == pytest.approx(0.08)
    assert [item["title"] for item in runs[0]["items"]] == [
        "Close the work line",
        "Move reading to haiku",
    ]
    assert {item["status"] for item in runs[0]["items"]} == {"new"}


def test_advice_mentions_are_expanded(transcripts: Path, db_path: Path) -> None:
    """A short id in a tip expands into a session name and a project (for the screen)."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", session="b2ae5a8a-1111-2222-3333-444455556666", ts=now),
            json.dumps(
                {
                    "type": "ai-title",
                    "sessionId": "b2ae5a8a-1111-2222-3333-444455556666",
                    "aiTitle": "project structure review",
                }
            ),
        ],
    )
    conn = connect(db_path)
    with conn:
        cursor = conn.execute(
            "INSERT INTO advice (ts, kind, digest_json, model, cost_usd) VALUES (?, 'manual',"
            " '{}', 'haiku', 0.07)",
            (now.isoformat(),),
        )
        conn.execute(
            """
            INSERT INTO advice_items (advice_id, key, title, severity, detail, action, evidence)
            VALUES (?, 'k1', 'Close session', 'crit', '', '', 'sessions[0]: b2ae5a8a, turns 7568')
            """,
            (cursor.lastrowid,),
        )
    conn.close()

    with client(db_path, transcripts) as api:
        item = api.get("/api/advice").json()["runs"][0]["items"][0]

    assert [s["title"] for s in item["sessions"]] == ["project structure review"]
    assert item["sessions"][0]["project"] == "project"
    assert item["sessions"][0]["id"].startswith("b2ae5a8a"), "the link leads to the full id"
    assert item["projects"] == ["project"], "the session brings its project along"


def test_a_tip_carries_the_ends_of_the_prompt_log(transcripts: Path, db_path: Path) -> None:
    """The card shows the first and the last prompt; the middle is asked for separately."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            prompt("start the dashboard", session="59d68098", ts=now - timedelta(hours=2)),
            prompt("now the tray", session="59d68098", ts=now - timedelta(hours=1)),
            prompt("and the diff", session="59d68098", ts=now),
            assistant("msg_1", session="59d68098", ts=now),
        ],
    )
    conn = connect(db_path)
    with conn:
        cursor = conn.execute(
            "INSERT INTO advice (ts, kind, digest_json, model, cost_usd) VALUES (?, 'manual',"
            " '{}', 'haiku', 0.05)",
            (now.isoformat(),),
        )
        conn.execute(
            """
            INSERT INTO advice_items (advice_id, key, title, severity, detail, action, evidence)
            VALUES (?, 'k1', 'Close the session', 'crit', '', '', 'session 59d68098 context 190k')
            """,
            (cursor.lastrowid,),
        )
    conn.close()

    with client(db_path, transcripts) as api:
        session = api.get("/api/advice").json()["runs"][0]["items"][0]["sessions"][0]
        whole = api.get("/api/sessions/59d68098/prompts").json()["prompts"]

    assert session["prompt_count"] == 3
    assert [row["text"] for row in session["prompts"]] == ["start the dashboard", "and the diff"]
    assert [row["text"] for row in whole] == ["start the dashboard", "now the tray", "and the diff"]


def test_advice_projects_are_read_from_the_text(transcripts: Path, db_path: Path) -> None:
    """A tip names a project without naming a session - the cut by project needs it too."""
    now = datetime.now(UTC)
    seed(transcripts, db_path, [assistant("msg_1", ts=now)])
    conn = connect(db_path)
    with conn:
        cursor = conn.execute(
            "INSERT INTO advice (ts, kind, digest_json, model, cost_usd) VALUES (?, 'manual',"
            " '{}', 'haiku', 0.07)",
            (now.isoformat(),),
        )
        conn.executemany(
            """
            INSERT INTO advice_items (advice_id, key, title, severity, detail, action, evidence)
            VALUES (?, ?, ?, 'warn', '', '', ?)
            """,
            [
                (cursor.lastrowid, "k1", "Split the 'project' work", "mechanical_opus = 212"),
                (cursor.lastrowid, "k2", "A project-wide habit", "cache_read = 0.9"),
                (cursor.lastrowid, "k3", "Move reading to haiku", "mechanical_opus = 212"),
            ],
        )
    conn.close()

    with client(db_path, transcripts) as api:
        items = api.get("/api/advice").json()["runs"][0]["items"]

    assert items[0]["projects"] == ["project"]
    assert items[1]["projects"] == [], "'project-wide' is another word, not the project"
    assert items[2]["projects"] == [], "a tip about the machine belongs to no project"


def test_advice_status_is_saved(transcripts: Path, db_path: Path) -> None:
    """A dismissed tip stays dismissed - "do not repeat" rests on that."""
    conn = connect(db_path)
    advice_run(conn)
    item_id = conn.execute("SELECT id FROM advice_items ORDER BY id LIMIT 1").fetchone()["id"]
    conn.close()

    with client(db_path, transcripts) as api:
        assert api.post(f"/api/advice/items/{item_id}?status=rejected").status_code == 200
        runs = api.get("/api/advice").json()["runs"]
        bad_status = api.post(f"/api/advice/items/{item_id}?status=maybe")
        missing = api.post("/api/advice/items/99999?status=accepted")

    assert runs[0]["items"][0]["status"] == "rejected"
    assert bad_status.status_code == 400
    assert missing.status_code == 404


def test_an_act_is_applied_only_against_the_diff_that_was_shown(
    transcripts: Path, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole door in one pass: a plan, a stale confirmation, a write and the way back."""
    claude = tmp_path / "claude"
    claude.mkdir()
    settings = claude / "settings.json"
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}, indent=2) + "\n")
    monkeypatch.setattr(paths, "CLAUDE_DIR", claude)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "state")

    conn = connect(db_path)
    advice_run(conn)
    item_id = conn.execute("SELECT id FROM advice_items ORDER BY id LIMIT 1").fetchone()["id"]
    with conn:
        conn.execute(
            "UPDATE advice_items SET act_json = ? WHERE id = ?",
            (json.dumps({"type": "allow_permission", "rule": "Bash(npm test:*)"}), item_id),
        )
    conn.close()

    with client(db_path, transcripts) as api:
        plan = api.post(f"/api/advice/items/{item_id}/plan").json()
        stale = api.post(f"/api/advice/items/{item_id}/apply", json={"hash": "not-what-was-shown"})
        applied = api.post(f"/api/advice/items/{item_id}/apply", json={"hash": plan["hash"]})
        item = api.get("/api/advice").json()["runs"][0]["items"][0]
        patches = api.get("/api/patches").json()["patches"]
        undone = api.post(f"/api/patches/{applied.json()['patch_id']}/rollback")

    assert '+      "Bash(npm test:*)"' in plan["diff"], "the diff is of the file, not of the intent"
    assert stale.status_code == 409, "a foreign change in between is a conflict, not an error"
    assert applied.status_code == 200
    assert item["status"] == "accepted", "a carried-out tip does not come round again"
    assert item["patch"]["status"] == "applied"
    assert [patch["kind"] for patch in patches] == ["allow_permission"]
    assert undone.status_code == 200
    assert json.loads(settings.read_text())["permissions"]["allow"] == ["Bash(ls)"]


def test_writes_are_refused_when_the_door_is_shut(
    transcripts: Path, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`actions.enabled = false` is the single switch for writes into a foreign config."""
    monkeypatch.setattr(paths, "CLAUDE_DIR", tmp_path / "claude")
    monkeypatch.setattr(
        config, "load", lambda *args: config.DEFAULTS | {"actions": {"enabled": False}}
    )
    conn = connect(db_path)
    advice_run(conn)
    item_id = conn.execute("SELECT id FROM advice_items ORDER BY id LIMIT 1").fetchone()["id"]
    conn.close()

    with client(db_path, transcripts) as api:
        assert api.post(f"/api/advice/items/{item_id}/plan").status_code == 403
        assert api.post(f"/api/advice/items/{item_id}/apply", json={"hash": ""}).status_code == 403


def test_a_tip_without_an_act_has_nothing_to_apply(transcripts: Path, db_path: Path) -> None:
    conn = connect(db_path)
    advice_run(conn)
    item_id = conn.execute("SELECT id FROM advice_items ORDER BY id LIMIT 1").fetchone()["id"]
    conn.close()

    with client(db_path, transcripts) as api:
        assert api.post(f"/api/advice/items/{item_id}/plan").status_code == 400
        assert api.get("/api/advice").json()["runs"][0]["items"][0]["act"] is None


def test_manual_run_is_labelled_manual(transcripts: Path, db_path: Path) -> None:
    """An analysis from the button is labelled "manual", not "hourly"."""
    now = datetime.now(UTC)
    seed(transcripts, db_path, [assistant("msg_1", ts=now - timedelta(minutes=5))])
    envelope = {
        "is_error": False,
        "total_cost_usd": 0.07,
        "structured_output": {
            "advice": [
                {
                    "title": "Close the work line",
                    "severity": "warn",
                    "detail": "",
                    "action": "",
                    "evidence": "chains[0].sessions = 19",
                }
            ]
        },
        "modelUsage": {"claude-haiku-4-5": {}},
    }

    with client(db_path, transcripts, advisor_run=lambda *args: envelope) as api:
        assert api.post("/api/advice/run?period=24h").status_code == 200
        runs = api.get("/api/advice").json()["runs"]

    assert runs[0]["kind"] == "manual"
    assert runs[0]["cost_usd"] == pytest.approx(0.07)


# --- SPEC §4 metrics (task B3) ---------------------------------------------------


def test_tool_profile_and_bash_commands(transcripts: Path, db_path: Path) -> None:
    """The tool profile, and inside Bash - by normalised commands."""
    now = datetime.now(UTC)
    bash = [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "git status"}}]
    read = [{"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "/x"}}]
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", ts=now - timedelta(minutes=1), content=bash),
            assistant("msg_2", uuid="u2", ts=now - timedelta(minutes=2), content=read),
            assistant(
                "msg_3",
                uuid="u3",
                ts=now - timedelta(minutes=3),
                content=[
                    {
                        "type": "tool_use",
                        "id": "b2",
                        "name": "Bash",
                        "input": {"command": "cd /tmp && git status"},
                    }
                ],
            ),
        ],
    )
    with client(db_path, transcripts) as api:
        profile = api.get("/api/overview").json()["tools"]

    assert profile["tools"][0] == {"tool": "Bash", "calls": 2}
    assert profile["tools_total"] == 3
    # Both commands collapsed into one row despite the `cd` prefix.
    assert profile["bash_commands"] == [{"command": "git status", "calls": 2}]


def test_model_share(transcripts: Path, db_path: Path) -> None:
    now = datetime.now(UTC)
    lines = [assistant("msg_1", ts=now - timedelta(minutes=1), output=100)]
    sonnet = json.loads(assistant("msg_2", uuid="u2", ts=now - timedelta(minutes=2), output=10))
    sonnet["message"]["model"] = "claude-sonnet-5"
    lines.append(json.dumps(sonnet))
    seed(transcripts, db_path, lines)

    with client(db_path, transcripts) as api:
        models = api.get("/api/overview").json()["models"]

    assert [row["model"] for row in models] == ["claude-opus-5", "claude-sonnet-5"]
    assert models[0]["turns"] == 1 and models[0]["output_tokens"] == 100


def test_idle_turns(transcripts: Path, db_path: Path) -> None:
    """An idle turn: an answer shorter than 10 tokens on a context larger than 50k."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            # Idle: a 3-token answer on a 60k context.
            assistant("msg_idle", ts=now - timedelta(minutes=1), output=3, cache_read=60_000),
            # A short answer, but the context is small - not idle.
            assistant(
                "msg_small", uuid="u2", ts=now - timedelta(minutes=2), output=3, cache_read=10
            ),
            # A large context, but the answer is large too - not idle.
            assistant(
                "msg_work", uuid="u3", ts=now - timedelta(minutes=3), output=900, cache_read=60_000
            ),
        ],
    )
    with client(db_path, transcripts) as api:
        idle = api.get("/api/overview").json()["idle"]

    assert idle["turns"] == 1
    assert idle["share"] == pytest.approx(1 / 3)
    assert idle["cache_read"] == 60_000
    assert (idle["max_output"], idle["min_context"]) == (10, 50_000)


def test_limit_window_starts_after_a_long_pause(transcripts: Path, db_path: Path) -> None:
    """The limit window starts with the first turn after a pause longer than five hours."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_old", ts=now - timedelta(hours=9), output=50),
            assistant("msg_new", uuid="u2", ts=now - timedelta(hours=2), output=70),
            assistant("msg_now", uuid="u3", ts=now - timedelta(minutes=5), output=30),
        ],
    )
    with client(db_path, transcripts) as api:
        limits = api.get("/api/overview").json()["limits"]

    assert limits["approximate"] is True
    assert limits["window_hours"] == 5
    started = datetime.fromisoformat(limits["started_at"])
    # The nine-hour-old turn belongs to the previous window, it does not count.
    assert (now - started) < timedelta(hours=5)
    assert limits["usage"]["turns"] == 2
    assert limits["usage"]["output_tokens"] == 100
    assert limits["week"]["turns"] == 3


def test_limit_window_empty_when_no_turns(db_path: Path, transcripts: Path) -> None:
    with client(db_path, transcripts) as api:
        limits = api.get("/api/overview").json()["limits"]
    assert limits["started_at"] is None
    assert limits["usage"] is None


# --- subscription limits -------------------------------------------------------


def test_plan_limits_reach_the_dashboard(transcripts: Path, db_path: Path) -> None:
    """Plan percentages are served as they are - Anthropic counts them, not us."""
    payload = {
        "source": "api",
        "fetched_at": 1_786_635_000.0,
        "plan": "max",
        "tier": "default_claude_max_5x",
        "limits": [
            {
                "kind": "session",
                "label": "current session",
                "percent": 48,
                "resets_at": "2026-08-13T16:10:00Z",
                "severity": "normal",
                "is_active": True,
            }
        ],
        "error": None,
    }
    seed(transcripts, db_path, [assistant("msg_1")])
    with client(db_path, transcripts, limits=StubLimits(payload)) as api:
        assert api.get("/api/overview").json()["plan"] == payload


def test_overview_stamps_point_at_last_events(
    transcripts: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The widget mark is the time of the last event, not the moment of recomputation."""
    now = datetime.now(UTC)
    # Daily marks are counted from local midnight, while the test seeds events "20 minutes
    # ago": started at 00:05 it would catch yesterday and fail. We push the day boundary
    # away, so that marks are checked rather than the clock on the wall.
    monkeypatch.setattr(
        metrics_module, "local_day_start", lambda moment: moment - timedelta(days=1)
    )
    seed(
        transcripts,
        db_path,
        [
            # An idle turn: a short answer on a large context.
            assistant(
                "msg_idle",
                uuid="u1",
                ts=now - timedelta(minutes=20),
                output=3,
                cache_read=200_000,
                content=[{"type": "text", "text": "ok"}],
            ),
            assistant("msg_tool", uuid="u2", ts=now - timedelta(minutes=10)),
            # The last turn without tools: the feed and the profile times will diverge.
            assistant(
                "msg_text",
                uuid="u3",
                ts=now - timedelta(minutes=1),
                content=[{"type": "text", "text": "done"}],
            ),
        ],
    )
    with client(db_path, transcripts) as api:
        stamps = api.get("/api/overview").json()["stamps"]

    assert stamps["last_turn"] == stamps["today_turn"]  # the turns are today's
    assert stamps["last_turn"] > stamps["tool_call"]  # the last turn has no tools
    assert stamps["idle_turn"] < stamps["tool_call"]  # the idle one came earlier


def test_overview_stamps_are_empty_without_turns(transcripts: Path, db_path: Path) -> None:
    """An empty slice is not a time but a dash: the widget has nothing to date."""
    seed(transcripts, db_path, [])
    with client(db_path, transcripts) as api:
        assert api.get("/api/overview").json()["stamps"] == {
            "last_turn": None,
            "today_turn": None,
            "tool_call": None,
            "idle_turn": None,
        }


def test_plan_refresh_asks_limits_now(transcripts: Path, db_path: Path) -> None:
    """The refresh button in the limits widget goes past the five-minute cache."""
    seed(transcripts, db_path, [assistant("msg_1")])
    limits = StubLimits()
    with client(db_path, transcripts, limits=limits) as api:
        response = api.post("/api/plan/refresh")
        assert response.status_code == 200
        assert response.json()["plan"] == limits.payload
        assert limits.refreshes == 1


def test_overview_shows_what_the_advisor_costs(transcripts: Path, db_path: Path) -> None:
    """An instrument that costs more than it saves is a bad instrument: its own
    spend is visible in "Overview" next to everyone else's (task C4)."""
    conn = connect(db_path)
    advice_run(conn, kind="hourly", cost=0.07)
    advice_run(conn, kind="weekly", cost=0.31)
    conn.close()

    with client(db_path, transcripts) as api:
        advisor = api.get("/api/overview").json()["advisor"]

    assert advisor["ticks"] == 2
    assert advisor["cost_usd"] == pytest.approx(0.38)
    assert advisor["by_kind"][0] == {"kind": "weekly", "ticks": 1, "cost_usd": pytest.approx(0.31)}


def test_overview_without_advisor_runs(transcripts: Path, db_path: Path) -> None:
    """While there were no analyses, the row does not appear in "Overview" at all."""
    connect(db_path).close()
    with client(db_path, transcripts) as api:
        advisor = api.get("/api/overview").json()["advisor"]
    assert advisor == {"ticks": 0, "cost_usd": 0, "last_at": None, "by_kind": []}
