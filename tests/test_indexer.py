"""Тесты импорта транскриптов в SQLite (задача A2)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cloudo_dash.collector.indexer import ingest_file, ingest_tree
from cloudo_dash.db import connect

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


# --- критерий готовности A2 --------------------------------------------------


def test_second_pass_changes_nothing(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Повторный вызов на том же файле не меняет ни одной строки."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [prompt("привет"), assistant("msg_1"), assistant("msg_2", uuid="u2")])

    first = ingest_file(conn, path)
    snapshot = rows(conn, "SELECT * FROM turns ORDER BY message_id")
    second = ingest_file(conn, path)

    assert first.turns_new == 2
    assert second.turns_new == 0
    assert second.lines == 0  # хвост дочитан, перечитывать нечего
    assert [tuple(row) for row in rows(conn, "SELECT * FROM turns ORDER BY message_id")] == [
        tuple(row) for row in snapshot
    ]


def test_reingest_after_offset_reset_is_idempotent(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Даже при перечитывании файла с нуля ходы не задваиваются."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1"), assistant("msg_2", uuid="u2")])
    ingest_file(conn, path)
    conn.execute("UPDATE files SET offset = 0")  # имитация сброса offset
    conn.commit()

    stats = ingest_file(conn, path)

    assert stats.lines == 2
    assert stats.turns_new == 0
    assert stats.turns_known == 2
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2


# --- ход собирается из нескольких записей ------------------------------------


def test_turn_split_across_records_is_one_row(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Три записи одного ответа дают один ход и один usage, а не три."""
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
    assert turn["output_tokens"] == 100  # не 300
    assert turn["cache_read"] == 1000
    assert turn["uuid"] == "u1"  # uuid первой записи хода
    tools = rows(conn, "SELECT * FROM tool_calls")
    assert [(row["tool"], row["detail"]) for row in tools] == [("Bash", "ls")]


def test_turn_blocks_split_between_passes(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Блоки хода, дочитанные следующим проходом, не создают второй ход."""
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
    """Повторный импорт того же блока tool_use гасится по tool_use_id."""
    path = tmp_path / "proj" / "s1.jsonl"
    block = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status"}}]
    write_transcript(path, [assistant("msg_1", content=block)])
    ingest_file(conn, path)
    conn.execute("UPDATE files SET offset = 0")
    conn.commit()
    ingest_file(conn, path)

    assert conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 1
    assert rows(conn, "SELECT detail FROM tool_calls")[0]["detail"] == "git status"


# --- resume: копии ходов в другом файле --------------------------------------


def test_resume_copy_does_not_double_count(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Скопированный при resume ход остаётся за первой сессией и не задваивает расход."""
    original = tmp_path / "proj" / "s1.jsonl"
    write_transcript(original, [assistant("msg_1", session="s1", output=100)])
    ingest_file(conn, original)

    resumed = tmp_path / "proj" / "s2.jsonl"
    write_transcript(
        resumed,
        [
            assistant("msg_1", session="s2", output=100),  # копия прошлого хода
            assistant("msg_2", session="s2", uuid="u2", output=7),  # новый ход
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
    """uuid записи не уникален по истории — на нём падать нельзя."""
    for name, session in (("s1.jsonl", "s1"), ("s2.jsonl", "s2")):
        path = tmp_path / "proj" / name
        write_transcript(path, [assistant(f"msg_{session}", session=session, uuid="одинаковый")])
        ingest_file(conn, path)
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2


# --- инкрементальность -------------------------------------------------------


def test_incremental_tail_read(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1")])
    first = ingest_file(conn, path)

    with path.open("a") as fh:
        fh.write(assistant("msg_2", uuid="u2") + "\n")
    second = ingest_file(conn, path)

    assert second.lines == 1  # прочитана только дописанная строка
    assert second.offset > first.offset
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2


def test_partial_line_is_not_consumed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Недописанная строка ждёт следующего прохода и не двигает offset."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1")])
    ingest_file(conn, path)

    tail = assistant("msg_2", uuid="u2")
    with path.open("a") as fh:
        fh.write(tail[:40])  # запись оборвана на середине
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

    write_transcript(path, [assistant("msg_3", uuid="u3")])  # файл усечён и переписан
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
    replacement.replace(path)  # пересоздание файла: inode другой, size больше
    stats = ingest_file(conn, path)

    assert stats.restarted
    assert stats.lines == 3
    assert stats.turns_new == 1
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 3


def test_missing_file_is_survivable(conn: sqlite3.Connection, tmp_path: Path) -> None:
    stats = ingest_file(conn, tmp_path / "нет-такого.jsonl")
    assert stats.lines == 0


def test_broken_lines_do_not_stop_the_pass(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1"), "{битый json", "", assistant("msg_2", uuid="u2")])
    stats = ingest_file(conn, path)
    assert stats.lines == 4
    assert stats.turns_new == 2


# --- сессии и проекты --------------------------------------------------------


def test_session_metadata_and_totals(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "мой-проект" / "s1.jsonl"
    write_transcript(
        path,
        [
            prompt("первый вопрос", ts="2026-08-13T09:00:00Z"),
            assistant("msg_1", ts="2026-08-13T09:00:05Z", output=10, cache_read=500, write_1h=20),
            prompt("второй вопрос", ts="2026-08-13T09:01:00Z"),
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
    assert session["first_prompt"] == "первый вопрос"
    assert session["started_at"] == "2026-08-13T09:00:00Z"
    assert session["last_at"] == "2026-08-13T09:01:05Z"
    assert session["turns"] == 2
    assert session["tokens_out"] == 40
    assert session["cache_read"] == 1400
    assert session["cache_write"] == 25
    assert session["last_context"] == 2 + 900 + 5  # контекст последнего хода

    project = rows(conn, "SELECT * FROM projects")[0]
    assert project["slug"] == "мой-проект"
    assert project["root_path"] == "/Users/x/project"
    assert session["project_id"] == project["id"]


def test_first_prompt_ignores_sidechain_and_compact_summary(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    sidechain = json.loads(prompt("задача сабагенту", ts="2026-08-13T08:00:00Z"))
    sidechain["isSidechain"] = True
    compact = json.loads(prompt("пересказ", ts="2026-08-13T08:30:00Z"))
    compact["isCompactSummary"] = True
    write_transcript(
        path,
        [
            json.dumps(sidechain),
            json.dumps(compact),
            prompt("настоящий промпт", ts="2026-08-13T09:00:00Z"),
        ],
    )
    ingest_file(conn, path)
    assert rows(conn, "SELECT first_prompt FROM sessions")[0]["first_prompt"] == "настоящий промпт"


def test_several_sessions_in_one_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Файл ≠ сессия: группировка идёт по полю записи."""
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
    """`<synthetic>` — служебный ответ с нулевым usage, ходом не является."""
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


def test_unknown_types_are_counted_not_stored(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Незнакомые записи пока только считаются: raw_events — задача B6."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(
        path,
        [json.dumps({"type": "attachment", "sessionId": "s1", "uuid": "a1"}), assistant("msg_1")],
    )
    stats = ingest_file(conn, path)
    assert stats.unknown == 1
    assert conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0] == 0


# --- фикстуры ----------------------------------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_fixture_ingest_matches_manual_count(
    conn: sqlite3.Connection, path: Path, tmp_path: Path
) -> None:
    """Суммы в БД сходятся с независимым подсчётом по сырому JSON."""
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


# --- неполный usage в записях хода -------------------------------------------


def test_zero_usage_record_does_not_hide_real_one(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Ход, начатый нулевой записью, получает расход из завершающей."""
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
    """Ход уже в БД с нулём: дочитанный хвост поднимает расход, а не задваивает его."""
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
    """Копия хода с нулевым расходом не затирает уже учтённый."""
    original = tmp_path / "proj" / "s1.jsonl"
    write_transcript(original, [assistant("msg_1", session="s1", output=100)])
    ingest_file(conn, original)

    copy = tmp_path / "proj" / "s2.jsonl"
    write_transcript(copy, [assistant("msg_1", session="s2", output=0, cache_read=0, write_1h=0)])
    ingest_file(conn, copy)

    assert rows(conn, "SELECT output_tokens FROM turns")[0]["output_tokens"] == 100


# --- чем закончилась сессия --------------------------------------------------


def test_pending_state_after_prompt(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Промпт без ответа означает, что запрос сейчас выполняется."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [assistant("msg_1"), prompt("новый вопрос", ts="2026-08-13T11:00:00Z")])
    ingest_file(conn, path)

    session = rows(conn, "SELECT * FROM sessions")[0]
    assert session["last_record_kind"] == "prompt"
    assert session["last_record_at"] == "2026-08-13T11:00:00Z"


def test_pending_state_clears_after_answer(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(path, [prompt("вопрос", ts="2026-08-13T11:00:00Z")])
    ingest_file(conn, path)
    assert rows(conn, "SELECT last_record_kind FROM sessions")[0]["last_record_kind"] == "prompt"

    with path.open("a") as fh:
        fh.write(assistant("msg_1", ts="2026-08-13T11:00:20Z") + "\n")
    ingest_file(conn, path)

    session = rows(conn, "SELECT * FROM sessions")[0]
    assert session["last_record_kind"] == "assistant"
    assert session["last_record_at"] == "2026-08-13T11:00:20Z"


def test_service_records_do_not_clear_pending(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Между промптом и ответом пишутся attachment и прочая служебка."""
    path = tmp_path / "proj" / "s1.jsonl"
    write_transcript(
        path,
        [
            prompt("вопрос", ts="2026-08-13T11:00:00Z"),
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
