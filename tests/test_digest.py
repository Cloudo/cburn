"""Тесты дайджеста периода (задача D1).

Главный тест здесь — про приватность: в дайджест не должно просочиться ни
одного слова переписки. Остальное проверяет, что советчику есть на что
опереться: тяжёлые сессии, цепочки resume, механическая работа на Opus.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cloudo_dash import pricing
from cloudo_dash.analyzer import digest
from cloudo_dash.collector.indexer import ingest_tree
from cloudo_dash.db import connect

SECRET = "секретное слово из переписки"


def assistant(
    message_id: str,
    *,
    session: str = "s1",
    ts: datetime | None = None,
    uuid: str = "u1",
    model: str = "claude-opus-5",
    output: int = 100,
    cache_read: int = 60_000,
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
            "cwd": "/Users/x/секретный-проект",
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
                        "ephemeral_5m_input_tokens": 0,
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


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "digest.db")


def seed(conn: sqlite3.Connection, tmp_path: Path, lines: list[str], name: str = "s.jsonl") -> None:
    root = tmp_path / "projects" / "-Users-x-секретный-проект"
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text("\n".join(lines) + "\n")
    ingest_tree(conn, tmp_path / "projects")


def test_digest_carries_no_conversation_text(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Ни промпта, ни ответа, ни названия сессии — только числа и имена."""
    seed(
        conn,
        tmp_path,
        [
            prompt("пожалуйста, почини вход по SSO для клиента ООО «Ромашка»"),
            title("починка входа по SSO"),
            assistant("msg_1", tools=[{"name": "Bash", "input": {"command": "grep -r пароль ."}}]),
        ],
    )

    payload = json.dumps(
        digest.build(conn, datetime.now(UTC) - timedelta(days=1)), ensure_ascii=False
    )

    assert SECRET not in payload
    assert "Ромашка" not in payload
    assert "SSO" not in payload, "названия сессий — это пересказ переписки"
    assert "пароль" not in payload, "аргументы команд отбрасываются ещё при разборе"
    assert "grep" in payload, "имя команды при этом видно"


def test_digest_fits_the_limit(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Дайджест укладывается в 20k токенов — ради этого все топы обрезаны."""
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
    """Тяжёлая сессия и линия работы видны советчику."""
    now = datetime.now(UTC)
    seed(
        conn,
        tmp_path,
        [assistant("msg_1", session="исток", ts=now - timedelta(hours=2), cache_read=400_000)],
    )
    seed(
        conn,
        tmp_path,
        [
            assistant("msg_1", session="продолжение", ts=now - timedelta(hours=2)),
            assistant("msg_2", session="продолжение", uuid="u2", ts=now - timedelta(minutes=10)),
        ],
        name="вторая.jsonl",
    )

    payload = digest.build(
        conn,
        now - timedelta(days=1),
        config={"thresholds": {"context_crit": 150_000}},
    )

    heavy = {row["id"]: row for row in payload["sessions"]}
    assert heavy["исток"]["over_context_limit"] is True
    assert [chain["root"] for chain in payload["chains"]] == ["исток"]
    assert payload["chains"][0]["sessions"] == 2


def test_digest_counts_mechanical_opus(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Ход, где были только чтение и поиск, — кандидат на модель попроще."""
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
    """Скрипт, прогнанный через heredoc, считается отдельно от обычных вызовов."""
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
