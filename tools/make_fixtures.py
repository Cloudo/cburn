"""Slicing anonymised transcript fixtures out of the real history (SPEC §11).

Run: `.venv/bin/python tools/make_fixtures.py` - it walks ~/.claude/projects,
takes one file per Claude Code version and puts a trimmed, anonymised slice into
tests/fixtures/transcripts/<version>.jsonl.

Only the service skeleton is kept from a record: types, the whole `usage`, tool
names, normalised bash commands, flags. Conversation text, tool arguments,
paths and real identifiers are not kept.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cburn.collector.parser import normalize_command  # noqa: E402
from cburn.paths import CLAUDE_PROJECTS_DIR  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "transcripts"

#: How many records to take from a file and from where (the file start is the dullest).
RECORDS_PER_FIXTURE = 60

#: Identifier fields: replaced with a deterministic pseudo-UUID, the parentUuid -> uuid
#: connectivity is preserved.
ID_KEYS = {
    "uuid",
    "parentUuid",
    "sessionId",
    "leafUuid",
    "sourceToolAssistantUUID",
    "promptId",
    "requestId",
    "id",
    "tool_use_id",
    "message_id",
}

#: Fields dropped entirely: arbitrary structures from tools,
#: where text can hide even inside key names.
DROP_KEYS = {"toolUseResult", "snapshot", "attachment", "messages"}

#: Fields carried over as they are: numbers, flags and service enumerations.
SAFE_KEYS = {
    "type",
    "role",
    "model",
    "usage",
    "stop_reason",
    "stop_sequence",
    "timestamp",
    "version",
    "isSidechain",
    "isCompactSummary",
    "isMeta",
    "userType",
    "entrypoint",
    "promptSource",
    "permissionMode",
    "origin",
    "effort",
    "name",
    "service_tier",
    "speed",
    "is_error",
    "level",
    "subtype",
}


def pseudo_id(value: str, prefix: str = "") -> str:
    """A deterministic pseudo-identifier of the same shape as the original."""
    digest = hashlib.sha256(value.encode()).hexdigest()
    if value.startswith(("msg_", "req_", "toolu_")):
        head = value.split("_", 1)[0]
        return f"{head}_{digest[:24]}"
    return "-".join((digest[:8], digest[8:12], digest[12:16], digest[16:20], digest[20:32]))


def anonymize(value: Any, key: str | None = None, tool: str | None = None) -> Any:
    """Anonymise an arbitrary piece of a record."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        current_tool = value.get("name") if value.get("type") == "tool_use" else tool
        for name, item in value.items():
            if name in DROP_KEYS:
                result[name] = "[dropped]"
                continue
            # A key name can be conversation text too (question-keys in tool
            # answers), so keys that do not look like service fields are dropped.
            safe_name = name if _is_field_name(name) else f"key{len(result)}"
            result[safe_name] = anonymize(item, key=name, tool=current_tool)
        return result
    if isinstance(value, list):
        return [anonymize(item, key=key, tool=tool) for item in value]
    if isinstance(value, str):
        return _anonymize_str(value, key, tool)
    return value


def _is_field_name(name: str) -> bool:
    """Whether a key name looks like a service field rather than a piece of text."""
    return len(name) <= 40 and all(char.isalnum() or char in "-_." for char in name)


def _anonymize_str(value: str, key: str | None, tool: str | None) -> str:
    if key in SAFE_KEYS:
        return value
    if key in ID_KEYS:
        return pseudo_id(value)
    if key == "command" and tool == "Bash":
        return normalize_command(value) or "cmd"
    if key == "cwd":
        return "/Users/user/project"
    if key == "gitBranch":
        return "main"
    if key in {"text", "thinking", "content"}:
        return f"[text {len(value)} chars]"
    return "[redacted]"


def pick_records(path: Path) -> list[dict[str, Any]]:
    """Take a slice of records around the first assistant turn with non-empty usage."""
    records: list[dict[str, Any]] = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    start = 0
    for index, record in enumerate(records):
        if record.get("type") != "assistant":
            continue
        usage = record.get("message", {}).get("usage")
        if isinstance(usage, dict) and usage.get("output_tokens"):
            start = max(0, index - 5)
            break
    return records[start : start + RECORDS_PER_FIXTURE]


def file_version(path: Path) -> str | None:
    """The Claude Code version a file was written by (from the first suitable record)."""
    with path.open(errors="replace") as fh:
        for _, line in zip(range(50), fh, strict=False):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            version = record.get("version") if isinstance(record, dict) else None
            if isinstance(version, str):
                return version
    return None


def main() -> int:
    if not CLAUDE_PROJECTS_DIR.exists():
        print(f"no directory {CLAUDE_PROJECTS_DIR}", file=sys.stderr)
        return 1
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    # Per version we take the largest file among the reasonably sized ones: small
    # transcripts often hold not a single assistant turn.
    by_version: dict[str, Path] = {}
    candidates = [p for p in CLAUDE_PROJECTS_DIR.rglob("*.jsonl") if p.stat().st_size < 20_000_000]
    for path in sorted(candidates, key=lambda p: p.stat().st_size, reverse=True):
        version = file_version(path)
        if version and version not in by_version:
            by_version[version] = path

    for version, path in sorted(by_version.items()):
        records = [anonymize(record) for record in pick_records(path)]
        if not records:
            continue
        target = FIXTURES_DIR / f"v{version}.jsonl"
        with target.open("w") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"{target.name}: {len(records)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
