"""Tests of the transcript line parser (task A1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cburn.collector.parser import (
    ParsedRecord,
    RecordKind,
    normalize_command,
    parse_line,
)

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "transcripts").glob("*.jsonl"))


def parse_fixture(path: Path) -> list[ParsedRecord]:
    return [record for line in path.read_text().splitlines() if (record := parse_line(line))]


def test_fixtures_present() -> None:
    """The fixtures must cover every Claude Code version from history."""
    assert {path.stem for path in FIXTURES} >= {
        "v2.1.220",
        "v2.1.222",
        "v2.1.228",
    }


# --- garbage on input ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "\n", "{broken json", '{"type": "assistant"', "null", "[1, 2]", '"a string"', "42"],
)
def test_broken_line_returns_none(raw: str) -> None:
    """A broken line does not bring parsing down and does not pretend to be a record."""
    assert parse_line(raw) is None


def test_record_without_type_is_unknown() -> None:
    record = parse_line('{"uuid": "u1"}')
    assert record is not None
    assert record.kind is RecordKind.UNKNOWN
    assert record.payload == {"uuid": "u1"}


# --- usage ------------------------------------------------------------------


def test_usage_splits_cache_write_by_ttl() -> None:
    """A write into the 1h cache is billed differently from the 5m one - counted separately."""
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
    """Without a TTL breakdown everything goes to 5m, but the total stays correct."""
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
    """Unknown or wrong field types must not break parsing."""
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": "m",
                "usage": {
                    "input_tokens": None,
                    "output_tokens": "a lot",
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


# --- a turn is not a record ---------------------------------------------------


def test_one_turn_is_split_across_records_with_identical_usage() -> None:
    """The answer is spread over blocks, the usage in each record is full and identical.

    It must not be summed across records - the turn key is `message_id`.
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


# --- tools --------------------------------------------------------------------


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
                        "input": {"command": "git commit -m 'secret'"},
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
    assert record.tools[0].detail is None  # the path never reaches the database
    assert record.tools[1].detail == "git commit"  # the arguments are dropped
    assert record.tools[0].tool_use_id == "t1"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status --short", "git status"),
        ("git commit -m 'a wordy message'", "git commit"),
        ("ls", "ls"),
        ("ls -la /Users/secret", "ls"),
        ("sed -n 1,50p file.py", "sed"),
        ("cd /Users/x && npm run build", "npm run"),  # `cd` is a prefix, not a command
        ("cat a.txt | grep password", "cat"),
        ("/usr/local/bin/python3 script.py", "python3"),  # a file name is not a subcommand
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
    """Privacy: no paths and no arguments are left in the normalised command."""
    assert "/Users" not in (normalize_command("grep -r pattern /Users/me/secret") or "")


# --- user: a prompt versus a tool result ----------------------------------------


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
    ["hello", [{"type": "text", "text": "hello"}]],
)
def test_prompt_text_from_both_content_shapes(content: object) -> None:
    """Prompt content comes both as a string and as an array of blocks."""
    record = parse_line(json.dumps({"type": "user", "message": {"content": content}}))
    assert record is not None
    assert record.kind is RecordKind.PROMPT
    assert record.prompt_text == "hello"


def test_prompt_without_prompt_source_is_still_a_prompt() -> None:
    """`promptSource` is present on less than 5% of user records - we do not rely on it."""
    record = parse_line(json.dumps({"type": "user", "message": {"content": "question"}}))
    assert record is not None
    assert record.kind is RecordKind.PROMPT
    assert record.prompt_source is None


def test_prompt_text_is_truncated() -> None:
    """The parser trims by the log limit; the caption is cut down where it is stored."""
    record = parse_line(json.dumps({"type": "user", "message": {"content": "x" * 5000}}))
    assert record is not None
    assert record.prompt_text is not None
    assert len(record.prompt_text) == 2000


def test_a_note_written_for_the_human_is_not_a_prompt() -> None:
    """An interrupt and the geometry of a pasted image are written by Claude Code."""
    interrupted = parse_line(
        json.dumps({"type": "user", "message": {"content": "[Request interrupted by user]"}})
    )
    with_image = parse_line(
        json.dumps(
            {
                "type": "user",
                "message": {"content": "[Image: original 2118x984] look at the needle"},
            }
        )
    )

    assert interrupted is not None and interrupted.prompt_text is None
    assert with_image is not None and with_image.prompt_text == "look at the needle"


def test_compact_summary_flag() -> None:
    """Auto-compaction shows as a flag on a user record, the `summary` type is gone."""
    line = json.dumps(
        {"type": "user", "isCompactSummary": True, "message": {"content": "a compacted retelling"}}
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


# --- common fields ---------------------------------------------------------------


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
        "unknown-type-from-the-future",
    ],
)
def test_other_types_go_to_unknown_with_payload(raw_type: str) -> None:
    """Unknown types are not lost: the indexer stashes them into raw_events."""
    record = parse_line(json.dumps({"type": raw_type, "uuid": "u1", "field": "value"}))
    assert record is not None
    assert record.kind is RecordKind.UNKNOWN
    assert record.raw_type == raw_type
    assert record.uuid == "u1"
    assert record.payload["field"] == "value"


# --- fixtures of real versions -----------------------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_fixture_parses_without_losses(path: Path) -> None:
    """Every fixture line parses, assistant turns carry full usage."""
    lines = path.read_text().splitlines()
    records = parse_fixture(path)
    assert len(records) == len(lines)

    assistants = [record for record in records if record.kind is RecordKind.ASSISTANT]
    assert assistants, "the fixture has no assistant turns"
    for record in assistants:
        assert record.usage is not None
        assert record.message_id
        assert record.model
        assert record.ts

    # The key finding of the survey: a write into the 1h cache occurs in real history.
    assert any(record.usage.cache_write_1h > 0 for record in assistants if record.usage)
    assert all(record.kind is not RecordKind.UNKNOWN or record.raw_type for record in records)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_fixture_usage_matches_jq_style_sum(path: Path) -> None:
    """The sum over turns (not over records) matches an independent count.

    The independent count goes by `message.id` from the raw JSON, the way `jq` would do it.
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
    """There are more assistant records than turns: one answer is spread over blocks."""
    assistants = [r for r in parse_fixture(path) if r.kind is RecordKind.ASSISTANT]
    turns = {record.message_id for record in assistants}
    assert len(assistants) > len(turns)


# --- merging usage inside a turn ----------------------------------------------------


def test_usage_merge_takes_elementwise_max() -> None:
    """Records of one turn carry the spend unevenly: some of them are zero."""
    from cburn.collector.parser import Usage

    empty = Usage()
    full = Usage(
        input_tokens=2, output_tokens=106, cache_read=5000, cache_write_5m=1, cache_write_1h=7
    )
    assert empty.merge(full) == full
    assert full.merge(empty) == full  # the read order does not matter
    assert full.merge(full) == full  # a repeat is not summed


def test_usage_merge_combines_partial_records() -> None:
    from cburn.collector.parser import Usage

    merged = Usage(output_tokens=10, cache_read=5).merge(Usage(input_tokens=3, cache_write_1h=7))
    assert merged == Usage(input_tokens=3, output_tokens=10, cache_read=5, cache_write_1h=7)


# --- service wrappers in prompts -----------------------------------------------------


def test_service_blocks_are_stripped_from_prompt() -> None:
    """The session caption is the human's question, not the IDE context around it."""
    content = (
        "<ide_opened_file>The user opened the file /Users/x/ROADMAP.md</ide_opened_file>"
        "what is next on the plan?"
    )
    record = parse_line(json.dumps({"type": "user", "message": {"content": content}}))
    assert record is not None
    assert record.prompt_text == "what is next on the plan?"


def test_several_service_blocks_are_stripped() -> None:
    content = (
        "<system-reminder>a reminder</system-reminder>\n"
        "<local-command-caveat>a caveat</local-command-caveat>\n"
        "fix the tests"
    )
    record = parse_line(json.dumps({"type": "user", "message": {"content": content}}))
    assert record is not None
    assert record.prompt_text == "fix the tests"


def test_angle_brackets_inside_normal_prompt_survive() -> None:
    """Ordinary text with angle brackets does not count as a service block."""
    content = "why does `a <b> c` not parse?"
    record = parse_line(json.dumps({"type": "user", "message": {"content": content}}))
    assert record is not None
    assert record.prompt_text == content


# --- the session title and slash commands ---------------------------------------------


def test_a_bare_slash_command_is_not_a_prompt() -> None:
    """Running a slash command is a caveat and command blocks, without live text.

    The arguments live in a block of their own, so all that is left of a command is its
    name - nothing to read in the log, and `/clear` starts almost every session (C7).
    """
    content = (
        "<local-command-caveat>Caveat: The messages below were generated by the user while "
        "running local commands. DO NOT respond to these messages.</local-command-caveat>\n"
        "<command-name>/clear</command-name>\n<command-message>clear</command-message>"
    )
    record = parse_line(json.dumps({"type": "user", "message": {"content": content}}))
    assert record is not None
    assert record.prompt_text is None


def test_service_only_prompt_has_no_text() -> None:
    """A wall of service text will not become the session caption."""
    content = "<local-command-caveat>Caveat: do not answer this</local-command-caveat>"
    record = parse_line(json.dumps({"type": "user", "message": {"content": content}}))
    assert record is not None
    assert record.prompt_text is None


@pytest.mark.parametrize(
    ("raw_type", "field", "source"),
    [("ai-title", "aiTitle", "ai"), ("custom-title", "customTitle", "custom")],
)
def test_title_records(raw_type: str, field: str, source: str) -> None:
    record = parse_line(json.dumps({"type": raw_type, field: "Roadmap review", "sessionId": "s1"}))
    assert record is not None
    assert record.kind is RecordKind.TITLE
    assert record.title == "Roadmap review"
    assert record.title_source == source
    assert record.session_id == "s1"


def test_title_record_without_value_is_unknown() -> None:
    record = parse_line(json.dumps({"type": "ai-title", "sessionId": "s1"}))
    assert record is not None
    assert record.kind is RecordKind.UNKNOWN


# --- command normalisation: wrappers and subcommands ------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # In history `cd` turned out to be the most frequent "command" - it is a prefix,
        # not what the turn actually does.
        ("cd /Users/x/project && npm run build", "npm run"),
        ("cd /a && cd /b && make test", "make test"),
        ("cd ~/code", "cd"),  # a directory change is the whole command
        ("S=/tmp/secret python3 script.py", "python3"),  # an assignment is not a command
        ("env FOO=1 pytest -q", "pytest"),
        ("sudo systemctl restart nginx", "systemctl restart"),
        ("time make build", "make build"),
        # A subcommand only for commands from the allowlist: otherwise a file name
        # reaches the database against the privacy requirement.
        ("cat README", "cat"),
        ("cat a | grep b", "cat"),
        ("git commit -m 'message'", "git commit"),
        ("docker compose up -d", "docker compose"),
        ("glab mr view 42", "glab mr"),
    ],
)
def test_normalize_command_wrappers(command: str, expected: str) -> None:
    assert normalize_command(command) == expected


def test_normalized_command_keeps_no_file_names() -> None:
    """Privacy: neither a path nor a file name is left in the normalised command."""
    for command in ("cat /Users/me/secrets.env", "cd /Users/me/private && ls", "vim notes.md"):
        result = normalize_command(command) or ""
        assert "/" not in result
        assert "secrets" not in result and "notes" not in result
