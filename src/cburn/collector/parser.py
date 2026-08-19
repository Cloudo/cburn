"""Parsing one JSONL transcript line (SPEC §2, task A1).

A pure function with no database or filesystem access: a line in, a `ParsedRecord` or
`None` out. Not a single exception escapes - a broken line goes to the log and is
skipped, and the walk offset moves on regardless.

The format is undocumented and changes between Claude Code versions, so parsing is
tolerant: unknown fields are ignored, unknown record types are returned as
`RecordKind.UNKNOWN` with the raw payload (the indexer puts it into `raw_events`).

What matters about the data model, verified against real history:

* **A record is not a turn.** One assistant answer is spread over several JSONL records -
  one per content block (`thinking`, `text`, every `tool_use`), and each one carries the
  *full and identical* `usage`. The turn key is `message_id`; usage must not be summed
  across records, otherwise the spend inflates (by ×4.6 on average).
* **`uuid` is not unique across history.** On resume Claude Code copies past turns into
  a new file with a new `session_id`, keeping `uuid` and `message_id` (copies of one turn
  were seen in 20 files). Merging them is the indexer's job.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import orjson

log = logging.getLogger(__name__)

#: Record types parsed into a structure. Everything else is UNKNOWN.
_ASSISTANT = "assistant"
_USER = "user"

#: The session title. `custom-title` is set by a human and outranks the generated
#: `ai-title`; neither record has a timestamp or a uuid - only sessionId.
_TITLE_FIELDS = {"ai-title": "aiTitle", "custom-title": "customTitle"}

#: Claude Code writes the session's last prompt as a separate record - there is no need
#: to hunt for it by scanning user records.
_LAST_PROMPT = "last-prompt"

#: Limit on the prompt as a session caption: on screen a longer one does not fit anyway.
PROMPT_LIMIT = 200

#: Limit on the prompt in the log (task C7). The caption needs a line, the log is read
#: by a human, so it keeps more - but still bounded: a pasted file must not settle in
#: the database in full.
PROMPT_LOG_LIMIT = 2000


class RecordKind(StrEnum):
    """The meaning class of a record - what it is for spend accounting."""

    ASSISTANT = "assistant"  # a model answer (or a block of it), carries usage
    PROMPT = "prompt"  # a real user prompt
    TOOL_RESULT = "tool_result"  # a tool result, arrives as a user record
    TITLE = "title"  # session title: ai-title or custom-title
    LAST_PROMPT = "last_prompt"  # the session's last prompt, as a separate record
    UNKNOWN = "unknown"  # everything else goes to raw_events


@dataclass(frozen=True, slots=True)
class Usage:
    """The spend of one turn. Writes into the 5m and 1h cache are billed differently."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0

    @property
    def cache_write(self) -> int:
        """Total cache write."""
        return self.cache_write_5m + self.cache_write_1h

    @property
    def context_estimate(self) -> int:
        """An estimate of the context window in use at the moment of the turn (SPEC §4)."""
        return self.input_tokens + self.cache_read + self.cache_write

    def merge(self, other: Usage) -> Usage:
        """Merge the usage of two records of one turn - by element-wise maximum.

        Records of one answer usually carry identical usage, but for 2,527 turns out of
        15,197 in real history part of the records is zero: the spend is filled in when the
        answer completes, while the intermediate blocks are already written. The maximum
        gives the final value regardless of read order; a sum would inflate the spend
        several times over, and the first record would understate it by a third.
        """
        return Usage(
            input_tokens=max(self.input_tokens, other.input_tokens),
            output_tokens=max(self.output_tokens, other.output_tokens),
            cache_read=max(self.cache_read, other.cache_read),
            cache_write_5m=max(self.cache_write_5m, other.cache_write_5m),
            cache_write_1h=max(self.cache_write_1h, other.cache_write_1h),
        )


@dataclass(frozen=True, slots=True)
class ToolUse:
    """A tool call from a `tool_use` block."""

    tool: str
    tool_use_id: str | None = None
    detail: str | None = None  # for Bash - the normalised command


@dataclass(frozen=True, slots=True)
class ParsedRecord:
    """A parsed transcript line."""

    kind: RecordKind
    raw_type: str
    uuid: str | None = None
    parent_uuid: str | None = None
    session_id: str | None = None
    ts: str | None = None
    version: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    is_sidechain: bool = False
    # ASSISTANT
    message_id: str | None = None
    request_id: str | None = None
    model: str | None = None
    stop_reason: str | None = None
    usage: Usage | None = None
    tools: tuple[ToolUse, ...] = ()
    # PROMPT
    prompt_text: str | None = None
    prompt_source: str | None = None
    is_compact_summary: bool = False
    # TITLE
    title: str | None = None
    title_source: str | None = None  # ai | custom
    # UNKNOWN
    payload: dict[str, Any] = field(default_factory=dict)


def parse_line(raw: str) -> ParsedRecord | None:
    """Parse a transcript line. A broken or empty line gives `None`."""
    line = raw.strip()
    if not line:
        return None
    try:
        record = orjson.loads(line)
    except orjson.JSONDecodeError as exc:
        log.warning("unreadable transcript line: %s", exc)
        return None
    if not isinstance(record, dict):
        log.warning("transcript line is not an object: %s", type(record).__name__)
        return None
    try:
        return _parse_record(record)
    except Exception as exc:  # parsing must not stop the file walk
        log.warning("transcript line not parsed (%s): %s", record.get("type"), exc)
        return None


def _parse_record(record: dict[str, Any]) -> ParsedRecord:
    raw_type = _str(record.get("type")) or ""
    common: dict[str, Any] = {
        "raw_type": raw_type,
        "uuid": _str(record.get("uuid")),
        "parent_uuid": _str(record.get("parentUuid")),
        "session_id": _str(record.get("sessionId")),
        "ts": _str(record.get("timestamp")),
        "version": _str(record.get("version")),
        "cwd": _str(record.get("cwd")),
        "git_branch": _str(record.get("gitBranch")),
        "is_sidechain": bool(record.get("isSidechain")),
    }
    message = record.get("message")
    if not isinstance(message, dict):
        message = {}

    if raw_type == _ASSISTANT:
        return _parse_assistant(record, message, common)
    if raw_type == _USER:
        return _parse_user(record, message, common)
    if raw_type == _LAST_PROMPT:
        text = _str(record.get("lastPrompt"))
        if text:
            return ParsedRecord(
                kind=RecordKind.LAST_PROMPT,
                prompt_text=_clean_prompt(text),
                **common,
            )
    if raw_type in _TITLE_FIELDS:
        title = _str(record.get(_TITLE_FIELDS[raw_type]))
        if title:
            return ParsedRecord(
                kind=RecordKind.TITLE,
                title=title,
                title_source=raw_type.removesuffix("-title"),
                **common,
            )
    return ParsedRecord(kind=RecordKind.UNKNOWN, payload=record, **common)


def _parse_assistant(
    record: dict[str, Any], message: dict[str, Any], common: dict[str, Any]
) -> ParsedRecord:
    return ParsedRecord(
        kind=RecordKind.ASSISTANT,
        message_id=_str(message.get("id")),
        request_id=_str(record.get("requestId")),
        model=_str(message.get("model")),
        stop_reason=_str(message.get("stop_reason")),
        usage=_parse_usage(message.get("usage")),
        tools=_parse_tools(message.get("content")),
        **common,
    )


def _parse_user(
    record: dict[str, Any], message: dict[str, Any], common: dict[str, Any]
) -> ParsedRecord:
    """A prompt versus a tool result.

    They cannot be told apart by `promptSource`: the field is present on less than 5% of
    user records (values `sdk`, `typed`), and hand-typed prompts usually lack it.
    The reliable sign is a `tool_result` block in the content.
    """
    content = message.get("content")
    if _is_tool_result(content):
        return ParsedRecord(kind=RecordKind.TOOL_RESULT, **common)
    return ParsedRecord(
        kind=RecordKind.PROMPT,
        prompt_text=_prompt_text(content),
        prompt_source=_str(record.get("promptSource")),
        is_compact_summary=bool(record.get("isCompactSummary")),
        **common,
    )


def _is_tool_result(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)


#: Service blocks Claude Code mixes into a human prompt: IDE context, system
#: reminders, slash-command caveats. They are useless as a session caption -
#: the real question comes after them.
_SERVICE_BLOCK = re.compile(r"<([a-z][\w-]*)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_SERVICE_OPEN = re.compile(r"^\s*<[a-z][\w-]*>", re.IGNORECASE)
_COMMAND_NAME = re.compile(r"<command-name>\s*(.+?)\s*</command-name>", re.DOTALL | re.IGNORECASE)


#: A note Claude Code writes on the human's behalf: an interrupt, the geometry of a
#: pasted image, a nudge after an empty answer. Nobody typed those, so they are not
#: prompts - and out of 4225 records of the author's own history 405 were exactly this.
#: A tag a human writes themselves ("[bug] fix this") loses the tag - a fair price for
#: not having to keep a list of Claude Code's service phrases up to date.
_SERVICE_NOTE = re.compile(r"^(?:\[[^\]]*\]\s*)+")

#: A slash command leaves only its name in the transcript - the arguments sit in a
#: separate block - so in the log it is a line with nothing to read. And `/clear` starts
#: almost every session, so as "the first prompt" it says nothing about any of them.
_BARE_COMMAND = re.compile(r"^/[\w:.-]+$")


def _strip_service_blocks(text: str) -> str:
    """Keep only what the human actually wrote in the prompt.

    Running a slash command looks like a `local-command-caveat` warning plus
    `command-name`/`command-message` blocks - there is no live text there at all,
    and the command itself becomes the session caption.
    """
    stripped = _SERVICE_BLOCK.sub(" ", text).strip()
    if stripped:
        return stripped
    command = _COMMAND_NAME.search(text)
    if command:
        return command.group(1)
    # A single service text with no substance: let the caption be the session
    # title or the project - a wall of service warnings is no caption.
    return ""


def _clean_prompt(text: str) -> str | None:
    """Trim the prompt and strip the service wrappers.

    The trim is the log one: the caption is cut down to `PROMPT_LIMIT` where it is
    stored, and the whole thing is needed to be read by a human (task C7).
    """
    text = text.strip()
    if _SERVICE_OPEN.match(text):
        text = _strip_service_blocks(text)
    text = _SERVICE_NOTE.sub("", text).strip()
    if _BARE_COMMAND.match(text):
        return None
    return text[:PROMPT_LOG_LIMIT] or None


def _prompt_text(content: Any) -> str | None:
    """The prompt text, trimmed to `PROMPT_LOG_LIMIT`. Attachments are skipped."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = [
            _str(block.get("text")) or ""
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(part for part in parts if part)
    else:
        return None
    return _clean_prompt(text)


def _parse_usage(usage: Any) -> Usage | None:
    """Parsing `message.usage`.

    The breakdown of cache writes lives in `cache_creation`; old versions and synthetic
    records may lack it - then everything counts as the 5-minute one, so that the sum
    still matches `cache_creation_input_tokens`.
    """
    if not isinstance(usage, dict):
        return None
    creation = usage.get("cache_creation")
    if isinstance(creation, dict):
        write_5m = _int(creation.get("ephemeral_5m_input_tokens"))
        write_1h = _int(creation.get("ephemeral_1h_input_tokens"))
    else:
        write_5m = _int(usage.get("cache_creation_input_tokens"))
        write_1h = 0
    return Usage(
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        cache_read=_int(usage.get("cache_read_input_tokens")),
        cache_write_5m=write_5m,
        cache_write_1h=write_1h,
    )


def _parse_tools(content: Any) -> tuple[ToolUse, ...]:
    if not isinstance(content, list):
        return ()
    tools: list[ToolUse] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = _str(block.get("name"))
        if not name:
            continue
        tools.append(
            ToolUse(
                tool=name,
                tool_use_id=_str(block.get("id")),
                detail=_tool_detail(name, block.get("input")),
            )
        )
    return tuple(tools)


def _tool_detail(tool: str, tool_input: Any) -> str | None:
    """The call detail for the tool profile (SPEC §4).

    Only for Bash and only the normalised command - privacy demands that neither
    arguments nor paths settle in the database (SPEC §7).
    """
    if tool != "Bash" or not isinstance(tool_input, dict):
        return None
    return normalize_command(_str(tool_input.get("command")))


#: Tokens after which a new command begins.
_COMMAND_SEPARATORS = ("|", "&&", "||", ";", "\n")

#: Wrappers followed by the real command inside the same segment.
_WRAPPERS = {"sudo", "env", "time", "nohup", "exec", "command", "nice"}

#: `cd` is skipped whole together with its argument: the argument is a path,
#: not a command. In real history `cd` turned out to be the most frequent "command"
#: (2,291 calls), because it is almost always `cd somewhere && the thing you need`.
_PATH_WRAPPERS = {"cd", "pushd"}

#: Commands whose second word is a meaningful subcommand. An allowlist, not a
#: heuristic: otherwise `cat README` turns into "cat README", and a file name
#: leaks into the database against SPEC §7.
_SUBCOMMAND_HOSTS = {
    "git",
    "npm",
    "pnpm",
    "yarn",
    "docker",
    "make",
    "cargo",
    "go",
    "pip",
    "pip3",
    "uv",
    "poetry",
    "kubectl",
    "systemctl",
    "brew",
    "gh",
    "glab",
    "terraform",
    "helm",
    "apt",
    "apt-get",
    "dnf",
    "pacman",
    "gcloud",
    "aws",
    "az",
    "flatpak",
    "bundle",
    "rake",
    "mvn",
    "gradle",
    "dotnet",
    "composer",
    "deno",
    "bun",
}

#: A variable assignment before the command: `S=/tmp/x python3 ...`.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def normalize_command(command: str | None) -> str | None:
    """Collapse a bash command down to "first word + subcommand".

    `git commit -m "..."` → `git commit`, `sed -n 1,50p f.py` → `sed`,
    `cd /x && npm run build` becomes `npm run`. Arguments and paths are dropped:
    only the command name settles in the database (SPEC §7).

    A heredoc is marked separately (`python3 <<`): a script driven through the same
    chunk ten times over is a noticeable spend, yet by command name it is
    indistinguishable from an ordinary call. The script text is not stored.
    """
    if not command:
        return None
    heredoc = " <<" if "<<" in command else ""
    segments = _command_segments(command)
    for segment in segments:
        name = _command_name(segment)
        if name is not None:
            return name + heredoc
    # Only the directory change is left - it is the whole command then.
    return "cd" + heredoc if segments else None


def _command_segments(command: str) -> list[str]:
    """Split the line into commands by separators, keeping the order."""
    segments = [command.strip()]
    for separator in _COMMAND_SEPARATORS:
        segments = [part for segment in segments for part in segment.split(separator)]
    return [segment.strip() for segment in segments if segment.strip()]


def _command_name(segment: str) -> str | None:
    """The command name inside one segment; wrappers and assignments are skipped."""
    tokens = [token for token in segment.split() if not _ASSIGNMENT.match(token)]
    if not tokens:
        return None
    name = tokens[0].rsplit("/", 1)[-1]
    if not name or not name[0].isalnum():
        return None
    if name in _PATH_WRAPPERS:
        return None  # the argument is a path; the real command is in the next segment
    if name in _WRAPPERS:
        rest = " ".join(tokens[1:]).lstrip()
        return _command_name(rest) if rest else name
    if name in _SUBCOMMAND_HOSTS and len(tokens) > 1 and _looks_like_subcommand(tokens[1]):
        return f"{name} {tokens[1]}"
    return name


def _looks_like_subcommand(token: str) -> bool:
    return (
        2 <= len(token) <= 20
        and not token.startswith("-")
        and all(char.isalnum() or char in "-_" for char in token)
        and any(char.isalpha() for char in token)
    )


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
