"""Тесты HTTP и WebSocket (задача A5)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cloudo_dash import paths
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
    root = tmp_path / "projects" / "проект"
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
    """Лимиты в тестах не ходят ни в связку ключей, ни в сеть."""

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
) -> TestClient:
    """Тестовое приложение. По умолчанию живость «неизвестна»: тесты не должны
    запускать `claude agents --json`."""
    app = create_app(
        db_path=db_path,
        projects_dir=transcripts.parent,
        watch=watch,
        limits=limits or StubLimits(),  # type: ignore[arg-type]
        liveness=liveness,
    )
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
    assert data["burn"]["10s"]["turns"] == 0  # оба хода старше десяти секунд
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
    assert burn["5m"]["window_seconds"] == 300


# --- сессии ------------------------------------------------------------------


def test_sessions_list(transcripts: Path, db_path: Path) -> None:
    seed(transcripts, db_path, [prompt("вопрос"), assistant("msg_1")])
    with client(db_path, transcripts) as api:
        sessions = api.get("/api/sessions").json()["sessions"]
    assert [row["id"] for row in sessions] == ["s1"]
    assert sessions[0]["first_prompt"] == "вопрос"
    # Имя проекта — последний сегмент рабочего пути (cwd), а не имя каталога
    # транскриптов: slug вида `-Users-x-project` человеку ничего не говорит.
    assert sessions[0]["project"] == "project"


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


def test_frontend_cache_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Оболочку браузер сверяет каждый раз, ассеты с хешем кеширует навсегда.

    Без этого пересобранный фронт грузится в старой оболочке из кеша браузера.
    """
    from cloudo_dash.api import server

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>cloudo-dash</title>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    monkeypatch.setattr(server, "WEB_DIST", dist)

    app = server.create_app(db_path=tmp_path / "api.db", watch=False)
    with TestClient(app) as api:
        assert api.get("/").headers["cache-control"] == "no-cache"
        assert "immutable" in api.get("/assets/index-abc123.js").headers["cache-control"]


# --- самописец и живые показания ---------------------------------------------


def test_series_has_bucket_per_step(transcripts: Path, db_path: Path) -> None:
    """Лента самописца — сплошная сетка корзин, включая пустые."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            # Оба хода с одной меткой времени: иначе они попадают в соседние
            # корзины, когда замер приходится на границу шага.
            assistant("msg_1", ts=now - timedelta(seconds=8), output=100, cache_read=0),
            assistant("msg_2", uuid="u2", ts=now - timedelta(seconds=8), output=50, cache_read=0),
            assistant("msg_3", uuid="u3", ts=now - timedelta(minutes=2), output=10, cache_read=0),
        ],
    )
    with client(db_path, transcripts) as api:
        data = api.get("/api/overview").json()

    series = data["series"]
    assert data["series_bucket_seconds"] == 2
    assert len(series) >= 5 * 30  # пять минут по две секунды
    assert sum(bucket["turns"] for bucket in series) == 3
    assert sum(bucket["output_tokens"] for bucket in series) == 160
    assert any(bucket["turns"] == 0 for bucket in series), "пустые корзины не заполнены"
    # Соседние ходы попадают в одну корзину: шаг именно 2 секунды, а не секунда.
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


def test_ten_second_window_reacts_immediately(transcripts: Path, db_path: Path) -> None:
    """Короткое окно показывает, что происходит прямо сейчас, а не в среднем."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [assistant("msg_1", ts=now - timedelta(seconds=3), output=60, cache_read=0)],
    )
    with client(db_path, transcripts) as api:
        burn = api.get("/api/overview").json()["burn"]

    assert burn["10s"]["window_seconds"] == 10
    # Шесть секунд работы в десятисекундном окне — это 360 токенов в минуту.
    assert burn["10s"]["output_per_min"] == pytest.approx(360)
    assert burn["1m"]["output_per_min"] == pytest.approx(60)


def test_burn_window_carries_its_own_usage(transcripts: Path, db_path: Path) -> None:
    """Разбивка по составляющим доступна для каждого окна, не только за сегодня."""
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

    assert burn["10s"]["usage"]["cache_read"] == 900  # только свежий ход
    assert burn["10s"]["usage"]["output_tokens"] == 60
    assert burn["5m"]["usage"]["cache_read"] == 1000  # оба хода
    assert burn["5m"]["usage"]["cache_write"] == 100  # по 50 на ход


# --- закрытие сессии ---------------------------------------------------------


def test_hide_removes_session_from_dashboard(transcripts: Path, db_path: Path) -> None:
    seed(transcripts, db_path, [prompt("вопрос"), assistant("msg_1")])
    with client(db_path, transcripts) as api:
        assert len(api.get("/api/overview").json()["live_sessions"]) == 1

        assert api.post("/api/sessions/s1/hide").json() == {"session_id": "s1", "hidden": True}
        assert api.get("/api/overview").json()["live_sessions"] == []
        assert api.get("/api/sessions").json()["sessions"] == []

        api.post("/api/sessions/s1/hide", params={"hidden": False})
        assert len(api.get("/api/overview").json()["live_sessions"]) == 1


def test_hide_unknown_session_is_404(db_path: Path, transcripts: Path) -> None:
    with client(db_path, transcripts) as api:
        assert api.post("/api/sessions/нет-такой/hide").status_code == 404


def test_close_terminates_the_session_process(
    transcripts: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Процесс берётся по sessionId из списка Claude Code и получает SIGTERM."""
    from cloudo_dash.api import server
    from cloudo_dash.processes import ClaudeSession

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
    """Сессии уже нет среди запущенных — просто убираем карточку."""
    from cloudo_dash.api import server

    seed(transcripts, db_path, [assistant("msg_1")])
    monkeypatch.setattr(server, "process_for_session", lambda sid: None)

    with client(db_path, transcripts) as api:
        result = api.post("/api/sessions/s1/close").json()
        assert result["stopped"] is False
        assert result["pid"] is None
        assert "уже не запущен" in result["note"]
        assert api.get("/api/overview").json()["live_sessions"] == []


def test_live_sessions_are_sorted_by_activity(transcripts: Path, db_path: Path) -> None:
    """Самая свежая сессия сверху; сколько показывать — решает дашборд."""
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
    """Статус отвечает на вопрос, кого сессия ждёт."""
    now = datetime.now(UTC)
    tool_use = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]
    seed(
        transcripts,
        db_path,
        [
            # Модель работает: последняя запись — промпт без ответа.
            assistant("msg_a", session="working", ts=now - timedelta(seconds=40)),
            prompt("считай дальше", session="working", ts=now - timedelta(seconds=20)),
            # Модель ответила и ждёт человека.
            assistant("msg_b", session="answered", uuid="u2", ts=now - timedelta(seconds=30)),
            # Инструмент запрошен, результата нет — висит разрешение.
            assistant(
                "msg_c",
                session="permission",
                uuid="u3",
                ts=now - timedelta(seconds=60),
                content=tool_use,
                stop_reason="tool_use",
            ),
            # Тишина дольше двух минут.
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
    """Долгий инструмент - не висящее разрешение: у процесса есть свежий потомок.

    В транскрипте оба случая выглядят одинаково (запрос инструмента без
    ответа), разводит их только процесс: прогон тестов запускает потомка, а на
    вопросе «разрешить?» процесс простаивает.
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
        for session in ("гоняет", "спрашивает", "давний-потомок")
    ]
    seed(transcripts, db_path, lines)

    def liveness() -> dict[str, datetime | None]:
        return {
            "гоняет": asked + timedelta(seconds=1),  # потомок запущен по запросу
            "спрашивает": None,  # потомков нет — процесс ждёт человека
            "давний-потомок": asked - timedelta(hours=1),  # MCP-сервер, не в счёт
        }

    with client(db_path, transcripts, liveness=liveness) as api:
        data = api.get("/api/overview").json()

    statuses = {row["id"]: row["status"] for row in data["live_sessions"]}
    assert statuses == {
        "гоняет": "working",
        "спрашивает": "permission",
        "давний-потомок": "permission",
    }


def test_finished_session_leaves_idle(transcripts: Path, db_path: Path) -> None:
    """Молчащая сессия без процесса — «закончилась», а не «простаивает» (B4)."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_a", session="жива", ts=now - timedelta(minutes=20)),
            assistant("msg_b", session="умерла", uuid="u2", ts=now - timedelta(minutes=20)),
        ],
    )

    with client(db_path, transcripts, liveness=lambda: {"жива": None}) as api:
        data = api.get("/api/overview").json()

    statuses = {row["id"]: row["status"] for row in data["live_sessions"]}
    assert statuses == {"жива": "idle", "умерла": "done"}


def test_unknown_liveness_keeps_idle(transcripts: Path, db_path: Path) -> None:
    """Молчащий `claude` не повод объявить все сессии завершёнными (B4)."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [assistant("msg_a", session="тихая", ts=now - timedelta(minutes=20))],
    )

    with client(db_path, transcripts, liveness=lambda: None) as api:
        data = api.get("/api/overview").json()

    assert [row["status"] for row in data["live_sessions"]] == ["idle"]


# --- экран «Сессии» (задача C1) ----------------------------------------------


def test_sessions_page_filters_and_sparkline(transcripts: Path, db_path: Path) -> None:
    """Список фильтруется по проекту и статусу и несёт спарклайн расхода."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", session="живая", ts=now - timedelta(seconds=30)),
            assistant("msg_2", session="старая", uuid="u2", ts=now - timedelta(hours=3)),
        ],
    )

    with client(db_path, transcripts, liveness=lambda: {"живая": None}) as api:
        page = api.get("/api/sessions").json()
        only_done = api.get("/api/sessions?status=done").json()
        nothing = api.get("/api/sessions?project=нетакого").json()

    assert {row["id"] for row in page["sessions"]} == {"живая", "старая"}
    assert [row["id"] for row in only_done["sessions"]] == ["старая"]
    assert nothing["sessions"] == []
    assert page["projects"][0]["sessions"] == 2
    spark = next(row["spark"] for row in page["sessions"] if row["id"] == "живая")
    assert len(spark) == 24 and sum(spark) > 0


def test_sessions_page_period_cuts_old(transcripts: Path, db_path: Path) -> None:
    """Период отсекает старое: `24h` не показывает вчерашнюю сессию."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", session="свежая", ts=now - timedelta(minutes=5)),
            assistant("msg_2", session="вчерашняя", uuid="u2", ts=now - timedelta(days=2)),
        ],
    )

    with client(db_path, transcripts) as api:
        recent = api.get("/api/sessions?period=24h").json()["sessions"]
        everything = api.get("/api/sessions?period=all").json()["sessions"]

    assert [row["id"] for row in recent] == ["свежая"]
    assert len(everything) == 2


def test_sessions_page_marks_resume_chain(transcripts: Path, db_path: Path) -> None:
    """У продолжения виден родитель, у родителя — счётчик продолжений."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [assistant("msg_1", session="исток", ts=now - timedelta(minutes=30))],
    )
    seed(
        transcripts,
        db_path,
        [
            assistant("msg_1", session="продолжение", ts=now - timedelta(minutes=30)),
            assistant("msg_2", session="продолжение", uuid="u2", ts=now - timedelta(minutes=5)),
        ],
        "вторая.jsonl",
    )

    with client(db_path, transcripts) as api:
        rows = {row["id"]: row for row in api.get("/api/sessions").json()["sessions"]}

    assert rows["продолжение"]["parent_session_id"] == "исток"
    assert rows["исток"]["children"] == 1


def test_session_details_carry_turns_and_marks(transcripts: Path, db_path: Path) -> None:
    """Экран «Сессия»: ходы по порядку, холостые помечены, вехи собраны (C2)."""
    now = datetime.now(UTC)
    compacted = json.dumps(
        {
            "type": "user",
            "uuid": "compact-1",
            "sessionId": "s1",
            "timestamp": (now - timedelta(minutes=8)).isoformat().replace("+00:00", "Z"),
            "isCompactSummary": True,
            "message": {"role": "user", "content": "пересказ разговора"},
        }
    )
    seed(
        transcripts,
        db_path,
        [
            # Обычный ход и холостой: короткий ответ при большом контексте.
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


# --- экран «Настройки» (задача C3) -------------------------------------------


def test_config_is_read_and_written(
    transcripts: Path, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Настройки читаются, пишутся в файл и сразу применяют цены."""
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
    assert config_path.exists(), "конфиг должен лечь в файл, а не остаться в памяти"
    conn = connect(db_path, apply_schema=False)
    try:
        cost = conn.execute("SELECT cost_usd FROM turns WHERE message_id = 'msg_1'").fetchone()[0]
    finally:
        conn.close()
    assert cost > 0, "цены должны примениться сразу, без reindex"


def test_config_rejects_broken_values(
    transcripts: Path, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Негодные значения не доезжают до файла, а объясняются человеку."""
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(paths, "CONFIG_PATH", config_path)
    seed(transcripts, db_path, [assistant("msg_1")])

    with client(db_path, transcripts) as api:
        current = api.get("/api/config").json()["config"]
        current["thresholds"]["context_warn"] = 200_000  # жёлтая позже красной
        current["telegram"]["daily_summary_at"] = "вечером"
        response = api.put("/api/config", json={"config": current})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "жёлтая зона" in detail and "daily_summary_at" in detail
    assert not config_path.exists()


# --- метрики ТЗ §4 (задача B3) -----------------------------------------------


def test_tool_profile_and_bash_commands(transcripts: Path, db_path: Path) -> None:
    """Профиль инструментов, внутри Bash — по нормализованным командам."""
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
    # Обе команды свелись к одной строке, несмотря на префикс `cd`.
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
    """Холостой ход: ответ короче 10 токенов при контексте больше 50k."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            # Холостой: 3 токена ответа при контексте 60k.
            assistant("msg_idle", ts=now - timedelta(minutes=1), output=3, cache_read=60_000),
            # Короткий ответ, но контекст маленький — не холостой.
            assistant(
                "msg_small", uuid="u2", ts=now - timedelta(minutes=2), output=3, cache_read=10
            ),
            # Большой контекст, но и ответ большой — не холостой.
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
    """Окно лимитов начинается с первого хода после паузы длиннее пяти часов."""
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
    # Ход девятичасовой давности — из прошлого окна, он в счёт не идёт.
    assert (now - started) < timedelta(hours=5)
    assert limits["usage"]["turns"] == 2
    assert limits["usage"]["output_tokens"] == 100
    assert limits["week"]["turns"] == 3


def test_limit_window_empty_when_no_turns(db_path: Path, transcripts: Path) -> None:
    with client(db_path, transcripts) as api:
        limits = api.get("/api/overview").json()["limits"]
    assert limits["started_at"] is None
    assert limits["usage"] is None


# --- лимиты подписки ---------------------------------------------------------


def test_plan_limits_reach_the_dashboard(transcripts: Path, db_path: Path) -> None:
    """Проценты плана отдаются как есть — их считает Anthropic, не мы."""
    payload = {
        "source": "api",
        "fetched_at": 1_786_635_000.0,
        "plan": "max",
        "tier": "default_claude_max_5x",
        "limits": [
            {
                "kind": "session",
                "label": "текущая сессия",
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


def test_overview_stamps_point_at_last_events(transcripts: Path, db_path: Path) -> None:
    """Метка виджета — время последнего события, а не момент пересчёта."""
    now = datetime.now(UTC)
    seed(
        transcripts,
        db_path,
        [
            # Холостой ход: короткий ответ при большом контексте.
            assistant(
                "msg_idle",
                uuid="u1",
                ts=now - timedelta(minutes=20),
                output=3,
                cache_read=200_000,
                content=[{"type": "text", "text": "ок"}],
            ),
            assistant("msg_tool", uuid="u2", ts=now - timedelta(minutes=10)),
            # Последний ход без инструментов: у ленты и профиля времена разойдутся.
            assistant(
                "msg_text",
                uuid="u3",
                ts=now - timedelta(minutes=1),
                content=[{"type": "text", "text": "готово"}],
            ),
        ],
    )
    with client(db_path, transcripts) as api:
        stamps = api.get("/api/overview").json()["stamps"]

    assert stamps["last_turn"] == stamps["today_turn"]  # ходы сегодняшние
    assert stamps["last_turn"] > stamps["tool_call"]  # последний ход без инструментов
    assert stamps["idle_turn"] < stamps["tool_call"]  # холостой был раньше


def test_overview_stamps_are_empty_without_turns(transcripts: Path, db_path: Path) -> None:
    """Пустой срез — не время, а прочерк: виджету нечего датировать."""
    seed(transcripts, db_path, [])
    with client(db_path, transcripts) as api:
        assert api.get("/api/overview").json()["stamps"] == {
            "last_turn": None,
            "today_turn": None,
            "tool_call": None,
            "idle_turn": None,
        }


def test_plan_refresh_asks_limits_now(transcripts: Path, db_path: Path) -> None:
    """Кнопка обновления в виджете лимитов ходит мимо пятиминутного кэша."""
    seed(transcripts, db_path, [assistant("msg_1")])
    limits = StubLimits()
    with client(db_path, transcripts, limits=limits) as api:
        response = api.post("/api/plan/refresh")
        assert response.status_code == 200
        assert response.json()["plan"] == limits.payload
        assert limits.refreshes == 1
