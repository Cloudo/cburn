"""Тесты HTTP и WebSocket (задача A5)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cloudo_dash.api.server import create_app
from cloudo_dash.collector.indexer import ingest_tree
from cloudo_dash.db import connect
from cloudo_dash.metrics import TS_FORMAT


def assistant(
    message_id: str,
    *,
    session: str = "s1",
    uuid: str = "u1",
    ts: datetime | None = None,
    output: int = 100,
    cache_read: int = 1000,
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
                "content": [
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
    root = tmp_path / "projects" / "проект"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "api.db"


def seed(transcripts: Path, db_path: Path, lines: list[str]) -> None:
    (transcripts / "s1.jsonl").write_text("".join(line + "\n" for line in lines))
    conn = connect(db_path)
    ingest_tree(conn, transcripts.parent)
    conn.close()


def client(db_path: Path, transcripts: Path, *, watch: bool = False) -> TestClient:
    app = create_app(db_path=db_path, projects_dir=transcripts.parent, watch=watch)
    return TestClient(app)


# --- обзор -------------------------------------------------------------------


def test_overview_counts_recent_turns(transcripts: Path, db_path: Path) -> None:
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            prompt("первый вопрос"),
            assistant("msg_1", ts=now - timedelta(seconds=20), output=100),
            assistant("msg_2", uuid="u2", ts=now - timedelta(seconds=40), output=200),
            assistant("msg_old", uuid="u3", ts=now - timedelta(hours=5), output=999),
        ],
    )
    with client(db_path, transcripts) as api:
        data = api.get("/api/overview").json()

    assert data["totals"]["turns"] == 3
    assert data["burn"]["1m"]["turns"] == 2  # ход пятичасовой давности не в окне
    assert data["burn"]["1m"]["tokens_per_min"] == pytest.approx(2 * 2 + 300 + 2000 + 100)
    assert data["burn"]["60m"]["turns"] == 2
    assert data["today"]["output_tokens"] >= 300
    assert data["live_sessions"], "живая сессия не показана"
    assert data["live_sessions"][0]["id"] == "s1"
    assert data["top_sessions"][0]["id"] == "s1"


def test_overview_on_empty_db(db_path: Path, transcripts: Path) -> None:
    with client(db_path, transcripts) as api:
        data = api.get("/api/overview").json()
    assert data["totals"]["turns"] == 0
    assert data["burn"]["1m"]["tokens_per_min"] == 0
    assert data["live_sessions"] == []


def test_burn_rate_is_per_minute(transcripts: Path, db_path: Path) -> None:
    """Окно 5 минут делит на 5 — иначе стрелка врёт в пять раз."""
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


# --- сессии ------------------------------------------------------------------


def test_sessions_list(transcripts: Path, db_path: Path) -> None:
    seed(transcripts, db_path, [prompt("вопрос"), assistant("msg_1")])
    with client(db_path, transcripts) as api:
        sessions = api.get("/api/sessions").json()["sessions"]
    assert [row["id"] for row in sessions] == ["s1"]
    assert sessions[0]["first_prompt"] == "вопрос"
    assert sessions[0]["project"] == "проект"


def test_session_details(transcripts: Path, db_path: Path) -> None:
    seed(transcripts, db_path, [prompt("вопрос"), assistant("msg_1", output=42)])
    with client(db_path, transcripts) as api:
        data = api.get("/api/sessions/s1").json()

    assert data["session"]["output_tokens"] == 42
    assert data["session"]["cache_write"] == 50
    assert data["models"] == [{"model": "claude-opus-5", "turns": 1, "output_tokens": 42}]
    assert data["tools"] == [{"tool": "Bash", "calls": 1}]


def test_unknown_session_is_404(db_path: Path, transcripts: Path) -> None:
    with client(db_path, transcripts) as api:
        assert api.get("/api/sessions/нет-такой").status_code == 404


def test_health(db_path: Path, transcripts: Path) -> None:
    with client(db_path, transcripts) as api:
        assert api.get("/api/health").json()["ok"] is True


def test_root_reports_missing_frontend(
    db_path: Path, transcripts: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пока фронт не собран, корень подсказывает, как это сделать."""
    from cloudo_dash.api import server

    monkeypatch.setattr(server, "WEB_DIST", tmp_path / "нет-сборки")
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
    """Дописанная строка транскрипта приводит к пушу в течение секунды."""
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
    assert elapsed < 1.0, f"пуш пришёл за {elapsed:.2f} с — критерий вехи A не выполнен"


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
    """После остановки приложения фоновый поток не остаётся."""
    import threading

    before = {thread.name for thread in threading.enumerate()}
    with client(db_path, transcripts, watch=True) as api:
        api.get("/api/health")
    time.sleep(0.3)
    after = {thread.name for thread in threading.enumerate()}
    assert "cdash-watcher" not in after - before


def test_built_frontend_is_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Собранный фронт раздаётся статикой с того же порта, что и API."""
    from cloudo_dash.api import server

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>cloudo-dash</title>")
    monkeypatch.setattr(server, "WEB_DIST", dist)

    app = server.create_app(db_path=tmp_path / "api.db", watch=False)
    with TestClient(app) as api:
        page = api.get("/")
        assert page.status_code == 200
        assert "cloudo-dash" in page.text
        assert api.get("/api/health").json()["ok"] is True  # API не перекрыт статикой


# --- самописец и живые показания ---------------------------------------------


def test_series_has_bucket_per_step(transcripts: Path, db_path: Path) -> None:
    """Лента самописца — сплошная сетка корзин, включая пустые."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", ts=now - timedelta(seconds=8), output=100, cache_read=0),
            assistant("msg_2", uuid="u2", ts=now - timedelta(seconds=7), output=50, cache_read=0),
            assistant("msg_3", uuid="u3", ts=now - timedelta(minutes=2), output=10, cache_read=0),
        ],
    )
    with client(db_path, transcripts) as api:
        data = api.get("/api/overview").json()

    series = data["series"]
    assert data["series_bucket_seconds"] == 5
    assert len(series) >= 5 * 12  # пять минут по пять секунд
    assert sum(bucket["turns"] for bucket in series) == 3
    assert sum(bucket["output_tokens"] for bucket in series) == 160
    assert any(bucket["turns"] == 0 for bucket in series), "пустые корзины не заполнены"
    # Соседние ходы попадают в одну корзину: шаг именно 5 секунд, а не секунда.
    busiest = max(series, key=lambda bucket: bucket["turns"])
    assert busiest["turns"] == 2


def test_pending_session_is_reported(transcripts: Path, db_path: Path) -> None:
    """Промпт без ответа — признак того, что запрос сейчас выполняется."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", ts=now - timedelta(seconds=30)),
            prompt("новый вопрос", ts=now - timedelta(seconds=3)),
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
            prompt("вопрос", ts=now - timedelta(seconds=20)),
            assistant("msg_1", ts=now - timedelta(seconds=5)),
        ],
    )
    with client(db_path, transcripts) as api:
        assert api.get("/api/overview").json()["pending_sessions"] == []


def test_ws_pushes_without_new_turns(transcripts: Path, db_path: Path) -> None:
    """Тикер шлёт обзор и в тишине: окна burn rate скользят сами по себе."""
    from cloudo_dash.api import server

    seed(transcripts, db_path, [assistant("msg_1")])
    with (
        client(db_path, transcripts, watch=False) as api,
        api.websocket_connect("/ws") as socket,
    ):
        socket.receive_json()  # кадр при подключении
        started = time.monotonic()
        message = socket.receive_json()  # кадр от тикера, файлов никто не трогал
        elapsed = time.monotonic() - started

    assert message["type"] == "overview"
    assert elapsed < server.TICK_SECONDS * 2
