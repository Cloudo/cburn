"""Тесты парсера строки транскрипта (задача A1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloudo_dash.collector.parser import (
    ParsedRecord,
    RecordKind,
    normalize_command,
    parse_line,
)

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "transcripts").glob("*.jsonl"))


def parse_fixture(path: Path) -> list[ParsedRecord]:
    return [record for line in path.read_text().splitlines() if (record := parse_line(line))]


def test_fixtures_present() -> None:
    """Фикстуры должны покрывать все версии Claude Code из истории."""
    assert {path.stem for path in FIXTURES} >= {
        "v2.1.220",
        "v2.1.222",
        "v2.1.228",
    }


# --- мусор на входе ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "\n", "{битый json", '{"type": "assistant"', "null", "[1, 2]", '"строка"', "42"],
)
def test_broken_line_returns_none(raw: str) -> None:
    """Битая строка не роняет разбор и не притворяется записью."""
    assert parse_line(raw) is None


def test_record_without_type_is_unknown() -> None:
    record = parse_line('{"uuid": "u1"}')
    assert record is not None
    assert record.kind is RecordKind.UNKNOWN
    assert record.payload == {"uuid": "u1"}


# --- usage ------------------------------------------------------------------


def test_usage_splits_cache_write_by_ttl() -> None:
    """Запись в 1h-кэш тарифицируется иначе, чем в 5m, — считаем раздельно."""
    line = json.dumps(
        {
            "type": "assistant",
            "uuid": "u1",
            "sessionId": "s1",
            "timestamp": "2026-08-13T10:00:00Z",
            "requestId": "req_1",
            "message": {
                "id": "msg_1",
                "model": "claude-opus-5",
                "stop_reason": "tool_use",
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 379,
                    "cache_read_input_tokens": 20742,
                    "cache_creation_input_tokens": 8800,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": 8000,
                        "ephemeral_5m_input_tokens": 800,
                    },
                },
            },
        }
    )
    record = parse_line(line)
    assert record is not None
    assert record.kind is RecordKind.ASSISTANT
    assert record.message_id == "msg_1"
    assert record.request_id == "req_1"
    assert record.model == "claude-opus-5"
    assert record.stop_reason == "tool_use"
    usage = record.usage
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read) == (2, 379, 20742)
    assert (usage.cache_write_1h, usage.cache_write_5m) == (8000, 800)
    assert usage.cache_write == 8800
    assert usage.context_estimate == 2 + 20742 + 8800


def test_usage_without_cache_creation_falls_back_to_flat_field() -> None:
    """Без разбивки по TTL всё уходит в 5m, но сумма остаётся верной."""
    line = json.dumps(
        {
            "type": "assistant",
            "message": {"id": "m", "usage": {"cache_creation_input_tokens": 500}},
        }
    )
    record = parse_line(line)
    assert record is not None and record.usage is not None
    assert record.usage.cache_write == 500
    assert record.usage.cache_write_5m == 500


def test_usage_ignores_junk_values() -> None:
    """Незнакомые или неверные типы полей не должны ломать разбор."""
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": "m",
                "usage": {
                    "input_tokens": None,
                    "output_tokens": "много",
                    "cache_read_input_tokens": 10,
                    "unknown_field": {"a": 1},
                },
            },
        }
    )
    record = parse_line(line)
    assert record is not None and record.usage is not None
    assert (record.usage.input_tokens, record.usage.output_tokens) == (0, 0)
    assert record.usage.cache_read == 10


def test_assistant_without_usage() -> None:
    record = parse_line('{"type": "assistant", "message": {"id": "m", "model": "x"}}')
    assert record is not None
    assert record.kind is RecordKind.ASSISTANT
    assert record.usage is None


# --- ход ≠ запись -----------------------------------------------------------


def test_one_turn_is_split_across_records_with_identical_usage() -> None:
    """Ответ разложен по блокам, usage в каждой записи полный и одинаковый.

    Суммировать его по записям нельзя — ключ хода `message_id`.
    """
    usage = {"output_tokens": 1432, "cache_read_input_tokens": 628079}
    blocks = [
        [{"type": "thinking", "thinking": "..."}],
        [{"type": "text", "text": "..."}],
        [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls -la"}}],
    ]
    records = [
        parse_line(
            json.dumps(
                {
                    "type": "assistant",
                    "uuid": f"u{index}",
                    "requestId": "req_1",
                    "message": {"id": "msg_1", "usage": usage, "content": content},
                }
            )
        )
        for index, content in enumerate(blocks)
    ]
    assert all(record is not None for record in records)
    assert {record.message_id for record in records if record} == {"msg_1"}
    assert {record.usage.output_tokens for record in records if record and record.usage} == {1432}
    tools = [tool for record in records if record for tool in record.tools]
    assert [tool.tool for tool in tools] == ["Bash"]
    assert tools[0].detail == "ls"


# --- инструменты ------------------------------------------------------------


def test_tool_use_blocks_are_collected() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": "m",
                "content": [
                    {"type": "text", "text": "..."},
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x"}},
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "Bash",
                        "input": {"command": "git commit -m 'секрет'"},
                    },
                    {"type": "tool_use", "name": "mcp__playwright__browser_click", "input": {}},
                ],
            },
        }
    )
    record = parse_line(line)
    assert record is not None
    assert [tool.tool for tool in record.tools] == [
        "Read",
        "Bash",
        "mcp__playwright__browser_click",
    ]
    assert record.tools[0].detail is None  # путь в БД не попадает
    assert record.tools[1].detail == "git commit"  # аргументы отброшены
    assert record.tools[0].tool_use_id == "t1"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status --short", "git status"),
        ("git commit -m 'многословное сообщение'", "git commit"),
        ("ls", "ls"),
        ("ls -la /Users/secret", "ls"),
        ("sed -n 1,50p file.py", "sed"),
        ("cd /Users/x && npm run build", "cd"),
        ("cat a.txt | grep пароль", "cat"),
        ("/usr/local/bin/python3 script.py", "python3"),  # имя файла — не подкоманда
        ("docker compose up -d", "docker compose"),
        ("echo 'hi' > /tmp/x", "echo"),
        ("  ", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_command(command: str | None, expected: str | None) -> None:
    assert normalize_command(command) == expected


def test_normalize_command_keeps_no_paths() -> None:
    """Приватность: в нормализованной команде не остаётся путей и аргументов."""
    assert "/Users" not in (normalize_command("grep -r pattern /Users/me/secret") or "")


# --- user: промпт против результата инструмента ------------------------------


def test_tool_result_is_not_a_prompt() -> None:
    line = json.dumps(
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]},
        }
    )
    record = parse_line(line)
    assert record is not None
    assert record.kind is RecordKind.TOOL_RESULT
    assert record.prompt_text is None


@pytest.mark.parametrize(
    "content",
    ["привет", [{"type": "text", "text": "привет"}]],
)
def test_prompt_text_from_both_content_shapes(content: object) -> None:
    """Контент промпта бывает и строкой, и массивом блоков."""
    record = parse_line(json.dumps({"type": "user", "message": {"content": content}}))
    assert record is not None
    assert record.kind is RecordKind.PROMPT
    assert record.prompt_text == "привет"


def test_prompt_without_prompt_source_is_still_a_prompt() -> None:
    """`promptSource` есть меньше чем у 5% user-записей — на него не опираемся."""
    record = parse_line(json.dumps({"type": "user", "message": {"content": "вопрос"}}))
    assert record is not None
    assert record.kind is RecordKind.PROMPT
    assert record.prompt_source is None


def test_prompt_text_is_truncated() -> None:
    record = parse_line(json.dumps({"type": "user", "message": {"content": "я" * 500}}))
    assert record is not None
    assert record.prompt_text is not None
    assert len(record.prompt_text) == 200


def test_compact_summary_flag() -> None:
    """Автосуммаризация видна флагом на user-записи, типа `summary` больше нет."""
    line = json.dumps(
        {"type": "user", "isCompactSummary": True, "message": {"content": "сжатый пересказ"}}
    )
    record = parse_line(line)
    assert record is not None
    assert record.kind is RecordKind.PROMPT
    assert record.is_compact_summary


def test_image_only_prompt_has_no_text() -> None:
    line = json.dumps({"type": "user", "message": {"content": [{"type": "image", "source": {}}]}})
    record = parse_line(line)
    assert record is not None
    assert record.kind is RecordKind.PROMPT
    assert record.prompt_text is None


# --- общие поля -------------------------------------------------------------


def test_common_fields_and_sidechain() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "uuid": "u1",
            "parentUuid": "u0",
            "sessionId": "s1",
            "timestamp": "2026-08-13T10:00:00Z",
            "version": "2.1.228",
            "cwd": "/Users/x/project",
            "gitBranch": "main",
            "isSidechain": True,
            "message": {"id": "m"},
        }
    )
    record = parse_line(line)
    assert record is not None
    assert (record.uuid, record.parent_uuid, record.session_id) == ("u1", "u0", "s1")
    assert record.ts == "2026-08-13T10:00:00Z"
    assert record.version == "2.1.228"
    assert record.git_branch == "main"
    assert record.is_sidechain


@pytest.mark.parametrize(
    "raw_type",
    [
        "attachment",
        "system",
        "mode",
        "last-prompt",
        "custom-title",
        "ai-title",
        "queue-operation",
        "file-history-snapshot",
        "file-history-delta",
        "permission-mode",
        "frame-link",
        "agent-name",
        "summary",
        "неизвестный-тип-из-будущего",
    ],
)
def test_other_types_go_to_unknown_with_payload(raw_type: str) -> None:
    """Незнакомые типы не теряются: их складывает в raw_events индексатор."""
    record = parse_line(json.dumps({"type": raw_type, "uuid": "u1", "поле": "значение"}))
    assert record is not None
    assert record.kind is RecordKind.UNKNOWN
    assert record.raw_type == raw_type
    assert record.uuid == "u1"
    assert record.payload["поле"] == "значение"


# --- фикстуры реальных версий ------------------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_fixture_parses_without_losses(path: Path) -> None:
    """Каждая строка фикстуры разбирается, ходы ассистента несут полный usage."""
    lines = path.read_text().splitlines()
    records = parse_fixture(path)
    assert len(records) == len(lines)

    assistants = [record for record in records if record.kind is RecordKind.ASSISTANT]
    assert assistants, "в фикстуре нет ходов ассистента"
    for record in assistants:
        assert record.usage is not None
        assert record.message_id
        assert record.model
        assert record.ts

    # Ключевое поле разведки: запись в 1h-кэш встречается в реальной истории.
    assert any(record.usage.cache_write_1h > 0 for record in assistants if record.usage)
    assert all(record.kind is not RecordKind.UNKNOWN or record.raw_type for record in records)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_fixture_usage_matches_jq_style_sum(path: Path) -> None:
    """Сумма по ходам (не по записям) совпадает с независимым подсчётом.

    Независимый подсчёт — по `message.id` из сырого JSON, как это сделал бы `jq`.
    """
    by_message: dict[str, int] = {}
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record.get("type") != "assistant":
            continue
        message = record["message"]
        usage = message.get("usage") or {}
        by_message[message["id"]] = usage.get("output_tokens", 0)
    expected = sum(by_message.values())

    parsed: dict[str, int] = {}
    for record in parse_fixture(path):
        if record.kind is RecordKind.ASSISTANT and record.usage and record.message_id:
            parsed[record.message_id] = record.usage.output_tokens
    assert sum(parsed.values()) == expected
    assert parsed.keys() == by_message.keys()


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_fixture_records_outnumber_turns(path: Path) -> None:
    """Записей ассистента больше, чем ходов: один ответ разложен по блокам."""
    assistants = [r for r in parse_fixture(path) if r.kind is RecordKind.ASSISTANT]
    turns = {record.message_id for record in assistants}
    assert len(assistants) > len(turns)


# --- слияние usage внутри хода -----------------------------------------------


def test_usage_merge_takes_elementwise_max() -> None:
    """Записи одного хода несут расход неравномерно: часть из них нулевая."""
    from cloudo_dash.collector.parser import Usage

    empty = Usage()
    full = Usage(
        input_tokens=2, output_tokens=106, cache_read=5000, cache_write_5m=1, cache_write_1h=7
    )
    assert empty.merge(full) == full
    assert full.merge(empty) == full  # порядок чтения не влияет
    assert full.merge(full) == full  # повтор не суммируется


def test_usage_merge_combines_partial_records() -> None:
    from cloudo_dash.collector.parser import Usage

    merged = Usage(output_tokens=10, cache_read=5).merge(Usage(input_tokens=3, cache_write_1h=7))
    assert merged == Usage(input_tokens=3, output_tokens=10, cache_read=5, cache_write_1h=7)


# --- служебные обёртки в промптах --------------------------------------------


def test_service_blocks_are_stripped_from_prompt() -> None:
    """Подпись сессии — вопрос человека, а не контекст IDE вокруг него."""
    content = (
        "<ide_opened_file>The user opened the file /Users/x/ROADMAP.md</ide_opened_file>"
        "что у нас дальше по плану?"
    )
    record = parse_line(json.dumps({"type": "user", "message": {"content": content}}))
    assert record is not None
    assert record.prompt_text == "что у нас дальше по плану?"


def test_several_service_blocks_are_stripped() -> None:
    content = (
        "<system-reminder>напоминание</system-reminder>\n"
        "<local-command-caveat>предупреждение</local-command-caveat>\n"
        "почини тесты"
    )
    record = parse_line(json.dumps({"type": "user", "message": {"content": content}}))
    assert record is not None
    assert record.prompt_text == "почини тесты"


def test_prompt_of_only_service_blocks_is_kept() -> None:
    """Если кроме обёрток ничего нет, пустая подпись хуже некрасивой."""
    content = "<ide_opened_file>The user opened a file</ide_opened_file>"
    record = parse_line(json.dumps({"type": "user", "message": {"content": content}}))
    assert record is not None
    assert record.prompt_text == content


def test_angle_brackets_inside_normal_prompt_survive() -> None:
    """Обычный текст с угловыми скобками не считается служебным блоком."""
    content = "почему `a <b> c` не парсится?"
    record = parse_line(json.dumps({"type": "user", "message": {"content": content}}))
    assert record is not None
    assert record.prompt_text == content
