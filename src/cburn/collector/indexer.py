"""Importing transcripts into SQLite (task A2).

A file is read only from the stored offset and only up to the last complete newline:
an unfinished tail waits for the next pass. The offset is reset when the file has been
recreated (the inode changed) or truncated (the size is smaller than remembered).

The import is idempotent: a turn is identified by `message_id`, a tool call by
`tool_use_id`, both fields UNIQUE. That also swallows the turn copies Claude Code
carries into a new file on resume.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import paths
from ..pricing import apply_costs
from .parser import PROMPT_LIMIT, ParsedRecord, RecordKind, Usage, parse_line

log = logging.getLogger(__name__)

#: Claude Code service answers ("No response requested", hitting the limit):
#: zero usage and no requestId, they are not spend.
SYNTHETIC_MODEL = "<synthetic>"


@dataclass
class IngestStats:
    """What one pass over a single file produced."""

    path: str
    lines: int = 0
    turns_new: int = 0
    turns_known: int = 0  # already in the database: a re-read tail or a resume copy
    tools_new: int = 0
    prompts: int = 0
    unknown: int = 0
    sessions: int = 0
    offset: int = 0
    restarted: bool = False  # the offset was reset: the file was recreated or truncated


@dataclass
class _Turn:
    """A turn assembled from several records sharing one `message_id`."""

    message_id: str
    session_id: str
    ts: str
    request_id: str | None = None
    uuid: str | None = None
    parent_uuid: str | None = None
    model: str | None = None
    is_sidechain: bool = False
    usage: Usage = field(default_factory=Usage)
    tools: list[tuple[str | None, str, str | None]] = field(default_factory=list)


@dataclass
class _Session:
    """Session metadata accumulated over its records in the file."""

    session_id: str
    started_at: str | None = None
    last_at: str | None = None
    first_prompt: str | None = None
    cwd: str | None = None
    last_record_kind: str | None = None  # what the session ends with at this record
    last_record_at: str | None = None
    title: str | None = None
    title_source: str | None = None
    last_prompt: str | None = None
    last_stop_reason: str | None = None
    last_turn_at: str | None = None  # the time of the turn whose stop_reason is remembered


def ingest_file(conn: sqlite3.Connection, path: Path) -> IngestStats:
    """Read a transcript file from the stored offset and write it into the database."""
    stats = IngestStats(path=str(path))
    try:
        file_stat = path.stat()
    except OSError as exc:
        log.warning("transcript file unavailable (%s): %s", path, exc)
        return stats

    offset, stats.restarted = _resume_offset(conn, path, file_stat.st_ino, file_stat.st_size)
    chunk, new_offset = _read_complete_lines(path, offset)
    stats.offset = new_offset

    turns: dict[str, _Turn] = {}
    sessions: dict[str, _Session] = {}
    unknown: list[tuple[int, ParsedRecord, str]] = []
    events: list[tuple[str, str, str]] = []
    prompts: list[tuple[str, str, str, str]] = []
    for line_no, raw in enumerate(chunk, start=1):
        stats.lines += 1
        record = parse_line(raw)
        if record is None:
            continue
        if record.kind is RecordKind.UNKNOWN:
            unknown.append((line_no, record, raw))
        # Auto-compaction: after it the context collapses, and the chart must show
        # why (task C2).
        if record.is_compact_summary and record.session_id and record.ts:
            events.append((record.session_id, record.ts, "compact"))
        _collect(record, turns, sessions, stats, prompts)

    with conn:  # one transaction per file: either the file is counted or the offset stays
        project_id = _upsert_project(conn, path, sessions)
        _upsert_sessions(conn, sessions, project_id)
        _insert_turns(conn, turns, stats)
        _store_unknown(conn, path, unknown)
        _store_events(conn, events)
        _store_prompts(conn, prompts)
        _link_parents(conn, turns)
        apply_costs(conn, turns.keys())
        _refresh_session_totals(conn, sessions.keys())
        _save_offset(
            conn, path, file_stat.st_ino, file_stat.st_size, new_offset, file_stat.st_mtime
        )

    stats.sessions = len(sessions)
    return stats


def ingest_tree(
    conn: sqlite3.Connection,
    root: Path,
    on_file: Callable[[int, int, Path], None] | None = None,
) -> list[IngestStats]:
    """Walk the whole transcript directory (task B2).

    `on_file(done, total, path)` is called after each file - the CLI draws progress from
    it. A separate background job was never needed: a full walk over 639 MB takes
    seconds, see README.
    """
    paths_to_read = sorted(root.rglob("*.jsonl"))
    results = []
    for index, path in enumerate(paths_to_read, start=1):
        results.append(ingest_file(conn, path))
        if on_file is not None:
            on_file(index, len(paths_to_read), path)
    return results


# --- reading the tail --------------------------------------------------------


def _resume_offset(conn: sqlite3.Connection, path: Path, inode: int, size: int) -> tuple[int, bool]:
    row = conn.execute(
        "SELECT inode, size, offset FROM files WHERE path = ?", (str(path),)
    ).fetchone()
    if row is None:
        return 0, False
    if row["inode"] != inode or size < row["size"]:
        log.info("file recreated or truncated, reading from the start: %s", path)
        return 0, True
    return int(row["offset"]), False


def _read_complete_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Read only complete lines from the offset; return them and the new offset.

    An unfinished last line (without a newline) is not returned and does not move
    the offset - Claude Code will finish writing it in a moment.
    """
    lines: list[str] = []
    position = offset
    with path.open("rb") as fh:
        fh.seek(offset)
        for chunk in fh:
            if not chunk.endswith(b"\n"):
                break
            position += len(chunk)
            lines.append(chunk.decode("utf-8", errors="replace"))
    return lines, position


# --- accumulating ------------------------------------------------------------


def _collect(
    record: ParsedRecord,
    turns: dict[str, _Turn],
    sessions: dict[str, _Session],
    stats: IngestStats,
    prompts: list[tuple[str, str, str, str]],
) -> None:
    if record.kind is RecordKind.LAST_PROMPT and record.session_id:
        # The record has no time: it always describes the current session state.
        session = sessions.setdefault(record.session_id, _Session(session_id=record.session_id))
        session.last_prompt = _caption(record.prompt_text)
        return
    if record.kind is RecordKind.TITLE and record.session_id:
        # Title records have neither time nor uuid - only sessionId.
        session = sessions.setdefault(record.session_id, _Session(session_id=record.session_id))
        if record.title_source == "custom" or session.title_source != "custom":
            session.title = record.title
            session.title_source = record.title_source
        return
    if record.session_id and record.ts:
        _touch_session(sessions, record)

    if record.kind is RecordKind.PROMPT:
        stats.prompts += 1
        # The log holds what the human typed, and only that: a subagent prompt is written
        # by the machine, and an auto-compaction summary is not a prompt at all (task C7).
        if (
            record.session_id
            and record.ts
            and record.uuid
            and record.prompt_text
            and not record.is_sidechain
            and not record.is_compact_summary
        ):
            prompts.append((record.session_id, record.uuid, record.ts, record.prompt_text))
        return
    if record.kind is RecordKind.UNKNOWN:
        stats.unknown += 1  # stashing into raw_events is task B6
        return
    if record.kind is not RecordKind.ASSISTANT:
        return
    if not (record.message_id and record.session_id and record.ts):
        return
    if record.model == SYNTHETIC_MODEL:
        return

    known = sessions.get(record.session_id)
    if known is not None and (known.last_turn_at is None or record.ts >= known.last_turn_at):
        known.last_turn_at = record.ts
        known.last_stop_reason = record.stop_reason

    turn = turns.get(record.message_id)
    if turn is not None:
        # Records of one turn carry the spend unevenly: for some it is still zero.
        # The right value is the element-wise maximum, see Usage.merge.
        turn.usage = turn.usage.merge(record.usage or Usage())
        turn.model = turn.model or record.model
        turn.request_id = turn.request_id or record.request_id
    else:
        turn = _Turn(
            message_id=record.message_id,
            session_id=record.session_id,
            ts=record.ts,
            request_id=record.request_id,
            uuid=record.uuid,
            parent_uuid=record.parent_uuid,
            model=record.model,
            is_sidechain=record.is_sidechain,
            usage=record.usage or Usage(),
        )
        turns[record.message_id] = turn
    for tool in record.tools:
        turn.tools.append((tool.tool_use_id, tool.tool, tool.detail))


def _touch_session(sessions: dict[str, _Session], record: ParsedRecord) -> None:
    assert record.session_id and record.ts
    session = sessions.setdefault(record.session_id, _Session(session_id=record.session_id))
    if session.started_at is None or record.ts < session.started_at:
        session.started_at = record.ts
    if session.last_at is None or record.ts > session.last_at:
        session.last_at = record.ts
    # Service records (attachment and the rest) do not affect "how the session ended":
    # any number of them can be written between a prompt and an answer.
    if record.kind is not RecordKind.UNKNOWN and (
        session.last_record_at is None or record.ts >= session.last_record_at
    ):
        session.last_record_kind = record.kind.value
        session.last_record_at = record.ts
    session.cwd = session.cwd or record.cwd
    # The session caption is the first real human prompt: neither a subagent nor
    # an auto-compaction summary fits that role.
    if (
        session.first_prompt is None
        and record.kind is RecordKind.PROMPT
        and record.prompt_text
        and not record.is_sidechain
        and not record.is_compact_summary
    ):
        session.first_prompt = _caption(record.prompt_text)


def _caption(text: str | None) -> str | None:
    """A prompt as a session caption: the log keeps the long text, a line is enough here."""
    return text[:PROMPT_LIMIT] if text else text


# --- writing -------------------------------------------------------------


def _project_slug(path: Path) -> str:
    """The project slug is the top-level directory inside the transcript directory.

    The parent directory name will not do here: subagent transcripts live in
    `<project>/<session>/subagents/`, and they used to spawn a pseudo-project
    "subagents" that swallowed the parent sessions too.
    """
    try:
        parts = path.relative_to(paths.CLAUDE_PROJECTS_DIR).parts
    except ValueError:
        return path.parent.name  # a file outside the transcript directory (tests, manual run)
    return parts[0] if len(parts) > 1 else ""


def project_name(root_path: str | None) -> str | None:
    """The human project name is the last segment of the working path.

    The slug stays the key of the directory in `~/.claude/projects`, but on screen it is
    useless: `-Users-me-code-myapp` reads worse than `myapp`.
    """
    return PurePosixPath(root_path).name or None if root_path else None


def _upsert_project(
    conn: sqlite3.Connection, path: Path, sessions: dict[str, _Session]
) -> int | None:
    slug = _project_slug(path)
    if not slug:
        return None
    root_path = next((s.cwd for s in sessions.values() if s.cwd), None)
    conn.execute(
        """
        INSERT INTO projects (slug, root_path, display_name) VALUES (?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            root_path    = COALESCE(projects.root_path, excluded.root_path),
            display_name = COALESCE(projects.display_name, excluded.display_name)
        """,
        (slug, root_path, project_name(root_path)),
    )
    row = conn.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
    return int(row["id"]) if row else None


def _upsert_sessions(
    conn: sqlite3.Connection, sessions: dict[str, _Session], project_id: int | None
) -> None:
    conn.executemany(
        """
        INSERT INTO sessions (id, project_id, started_at, last_at, first_prompt,
                              last_record_kind, last_record_at, title, title_source,
                              last_prompt, last_stop_reason)
        VALUES (:id, :project_id, :started_at, :last_at, :first_prompt,
                :last_record_kind, :last_record_at, :title, :title_source,
                :last_prompt, :last_stop_reason)
        ON CONFLICT(id) DO UPDATE SET
            project_id   = COALESCE(excluded.project_id, sessions.project_id),
            started_at   = MIN(COALESCE(sessions.started_at, excluded.started_at),
                               excluded.started_at),
            last_at      = MAX(COALESCE(sessions.last_at, excluded.last_at), excluded.last_at),
            first_prompt = COALESCE(sessions.first_prompt, excluded.first_prompt),
            -- A batch of only service records arrives without last_record_at, and NULL
            -- in a scalar MAX would wipe the column, and the idle countdown with it.
            last_record_kind = CASE
                WHEN excluded.last_record_at IS NULL THEN sessions.last_record_kind
                WHEN excluded.last_record_at >= COALESCE(sessions.last_record_at, '')
                THEN excluded.last_record_kind ELSE sessions.last_record_kind END,
            last_record_at = MAX(COALESCE(sessions.last_record_at, excluded.last_record_at),
                                 COALESCE(excluded.last_record_at, sessions.last_record_at)),
            -- A title set by a human is never overwritten by a generated one.
            title = CASE
                WHEN excluded.title IS NULL THEN sessions.title
                WHEN sessions.title_source = 'custom' AND excluded.title_source <> 'custom'
                THEN sessions.title ELSE excluded.title END,
            title_source = CASE
                WHEN excluded.title_source IS NULL THEN sessions.title_source
                WHEN sessions.title_source = 'custom' AND excluded.title_source <> 'custom'
                THEN sessions.title_source ELSE excluded.title_source END,
            last_prompt = COALESCE(excluded.last_prompt, sessions.last_prompt),
            last_stop_reason = COALESCE(excluded.last_stop_reason, sessions.last_stop_reason)
        """,
        [
            {
                "id": session.session_id,
                "project_id": project_id,
                "started_at": session.started_at,
                "last_at": session.last_at,
                "first_prompt": session.first_prompt,
                "last_record_kind": session.last_record_kind,
                "last_record_at": session.last_record_at,
                "title": session.title,
                "title_source": session.title_source,
                "last_prompt": session.last_prompt,
                "last_stop_reason": session.last_stop_reason,
            }
            for session in sessions.values()
        ],
    )


def _insert_turns(conn: sqlite3.Connection, turns: dict[str, _Turn], stats: IngestStats) -> None:
    if not turns:
        return
    before = _count(conn, "turns")
    conn.executemany(
        """
        INSERT INTO turns (
            message_id, session_id, request_id, uuid, parent_uuid, ts, model, role,
            is_sidechain, input_tokens, output_tokens, cache_read,
            cache_write_5m, cache_write_1h, context_estimate
        ) VALUES (
            :message_id, :session_id, :request_id, :uuid, :parent_uuid, :ts, :model, 'assistant',
            :is_sidechain, :input_tokens, :output_tokens, :cache_read,
            :cache_write_5m, :cache_write_1h, :context_estimate
        )
        ON CONFLICT(message_id) DO UPDATE SET
            input_tokens     = MAX(turns.input_tokens, excluded.input_tokens),
            output_tokens    = MAX(turns.output_tokens, excluded.output_tokens),
            cache_read       = MAX(turns.cache_read, excluded.cache_read),
            cache_write_5m   = MAX(turns.cache_write_5m, excluded.cache_write_5m),
            cache_write_1h   = MAX(turns.cache_write_1h, excluded.cache_write_1h),
            context_estimate = MAX(turns.context_estimate, excluded.context_estimate),
            model            = COALESCE(turns.model, excluded.model),
            request_id       = COALESCE(turns.request_id, excluded.request_id)
        """,
        [
            {
                "message_id": turn.message_id,
                "session_id": turn.session_id,
                "request_id": turn.request_id,
                "uuid": turn.uuid,
                "parent_uuid": turn.parent_uuid,
                "ts": turn.ts,
                "model": turn.model,
                "is_sidechain": int(turn.is_sidechain),
                "input_tokens": turn.usage.input_tokens,
                "output_tokens": turn.usage.output_tokens,
                "cache_read": turn.usage.cache_read,
                "cache_write_5m": turn.usage.cache_write_5m,
                "cache_write_1h": turn.usage.cache_write_1h,
                "context_estimate": turn.usage.context_estimate,
            }
            for turn in turns.values()
        ],
    )
    stats.turns_new = _count(conn, "turns") - before
    stats.turns_known = len(turns) - stats.turns_new

    ids = {
        row["message_id"]: int(row["id"])
        for row in conn.execute(
            f"SELECT id, message_id FROM turns WHERE message_id IN "  # noqa: S608
            f"({','.join('?' * len(turns))})",
            tuple(turns),
        )
    }
    rows = [
        (ids[turn.message_id], tool_use_id, tool, detail)
        for turn in turns.values()
        if turn.message_id in ids
        for tool_use_id, tool, detail in turn.tools
    ]
    if not rows:
        return
    tools_before = _count(conn, "tool_calls")
    conn.executemany(
        """
        INSERT INTO tool_calls (turn_id, tool_use_id, tool, detail) VALUES (?, ?, ?, ?)
        -- Command normalisation is derived from the data, and its rules change:
        -- on a re-read the detail is refreshed instead of getting stuck at the old value.
        ON CONFLICT(tool_use_id) DO UPDATE SET detail = excluded.detail
        """,
        rows,
    )
    stats.tools_new = _count(conn, "tool_calls") - tools_before


#: How many full samples to keep per (type, version) pair. Beyond that it is a counter:
#: unknown records run into tens of thousands, and a handful of samples is enough.
RAW_SAMPLE_LIMIT = 5


def _store_unknown(
    conn: sqlite3.Connection, path: Path, records: list[tuple[int, ParsedRecord, str]]
) -> None:
    """Stash unknown records: samples plus a counter per (type, version) pair.

    The transcript format is undocumented and changes between Claude Code versions.
    The counter answers "what showed up", the samples answer "what it looks like";
    the full payload beyond the first `RAW_SAMPLE_LIMIT` is not needed (task B6).
    """
    for line_no, record, raw in records:
        version = record.version or ""
        seen = conn.execute(
            """
            INSERT INTO raw_event_counts (type, version, seen, first_at, last_at)
            VALUES (:type, :version, 1, :ts, :ts)
            ON CONFLICT(type, version) DO UPDATE SET
                seen     = seen + 1,
                first_at = COALESCE(first_at, excluded.first_at),
                last_at  = COALESCE(excluded.last_at, last_at)
            RETURNING seen
            """,
            {"type": record.raw_type, "version": version, "ts": record.ts},
        ).fetchone()[0]
        if seen <= RAW_SAMPLE_LIMIT:
            conn.execute(
                """
                INSERT INTO raw_events (path, line_no, ts, type, version, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(path), line_no, record.ts, record.raw_type, version, raw.strip()),
            )


#: How many message_ids to ask about at a time: a file can hold thousands, and the
#: number of parameters in an SQLite query is limited.
_CHUNK = 500


def _store_prompts(conn: sqlite3.Connection, prompts: list[tuple[str, str, str, str]]) -> None:
    """The prompt log of a session (task C7). `uuid` keeps a re-read tail from doubling it."""
    if not prompts:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO prompts (session_id, uuid, ts, text) VALUES (?, ?, ?, ?)",
        prompts,
    )


def _store_events(conn: sqlite3.Connection, events: list[tuple[str, str, str]]) -> None:
    """Notable moments of a session. Re-reading the tail does not double them: UNIQUE."""
    if not events:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO session_events (session_id, ts, kind) VALUES (?, ?, ?)",
        events,
    )


def _link_parents(conn: sqlite3.Connection, turns: dict[str, _Turn]) -> None:
    """Link a session to the one it was continued from (task B5).

    On resume Claude Code copies past turns into a new file with a new `sessionId`,
    keeping `message.id`. Deduplication leaves such a turn with its first owner - which
    means the turns of this file recorded against someone else's session are exactly
    the copied history.

    The direction of the link is set by time, not by file read order: the parent is
    whichever of the two sessions started earlier. Otherwise the link would depend on
    whose file the walk hit first. The pair is compared by `(started_at, id)`, so the
    order is strict and a cycle is impossible. The link is set once.
    """
    by_session: dict[str, list[str]] = {}
    for message_id, turn in turns.items():
        by_session.setdefault(turn.session_id, []).append(message_id)

    for session_id, message_ids in by_session.items():
        shared: dict[str, int] = {}
        for start in range(0, len(message_ids), _CHUNK):
            chunk = message_ids[start : start + _CHUNK]
            rows = conn.execute(
                f"""
                SELECT session_id, COUNT(*) AS shared
                  FROM turns
                 WHERE message_id IN ({",".join("?" * len(chunk))})
                   AND session_id <> ?
                 GROUP BY session_id
                """,  # noqa: S608
                (*chunk, session_id),
            )
            for row in rows:
                shared[row["session_id"]] = shared.get(row["session_id"], 0) + row["shared"]
        if not shared:
            continue
        candidate = max(shared, key=lambda other: shared[other])
        pair = {
            row["id"]: row["started_at"] or ""
            for row in conn.execute(
                "SELECT id, started_at FROM sessions WHERE id IN (?, ?)",
                (session_id, candidate),
            )
        }
        if len(pair) < 2:
            continue
        older, younger = sorted(pair, key=lambda one: (pair[one], one))
        conn.execute(
            "UPDATE sessions SET parent_session_id = ? WHERE id = ? AND parent_session_id IS NULL",
            (older, younger),
        )
        log.info("session %s continues %s (shared turns %s)", younger, older, shared[candidate])


def _refresh_session_totals(conn: sqlite3.Connection, session_ids: Iterable[str]) -> None:
    """Recompute the aggregates of the affected sessions (heavy work runs in SQL)."""
    ids = tuple(session_ids)
    if not ids:
        return
    conn.execute(
        f"""
        UPDATE sessions SET
            turns       = COALESCE(totals.turns, 0),
            tokens_in   = COALESCE(totals.tokens_in, 0),
            tokens_out  = COALESCE(totals.tokens_out, 0),
            cache_read  = COALESCE(totals.cache_read, 0),
            cache_write = COALESCE(totals.cache_write, 0),
            cost_usd    = COALESCE(totals.cost_usd, 0),
            last_context = COALESCE(totals.last_context, 0)
        FROM (
            SELECT session_id,
                   COUNT(*)                AS turns,
                   SUM(input_tokens)       AS tokens_in,
                   SUM(output_tokens)      AS tokens_out,
                   SUM(cache_read)         AS cache_read,
                   SUM(cache_write_5m + cache_write_1h) AS cache_write,
                   SUM(cost_usd)           AS cost_usd,
                   (SELECT context_estimate FROM turns AS last
                     WHERE last.session_id = turns.session_id
                     ORDER BY last.ts DESC, last.id DESC LIMIT 1) AS last_context
              FROM turns
             GROUP BY session_id
        ) AS totals
        WHERE sessions.id = totals.session_id
          AND sessions.id IN ({",".join("?" * len(ids))})
        """,  # noqa: S608
        ids,
    )


def _save_offset(
    conn: sqlite3.Connection, path: Path, inode: int, size: int, offset: int, mtime: float
) -> None:
    conn.execute(
        """
        INSERT INTO files (path, inode, size, offset, mtime) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            inode = excluded.inode, size = excluded.size,
            offset = excluded.offset, mtime = excluded.mtime
        """,
        (str(path), inode, size, offset, mtime),
    )


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
