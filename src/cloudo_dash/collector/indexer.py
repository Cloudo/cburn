"""Импорт транскриптов в SQLite (задача A2).

Файл читается только с сохранённого offset и только до последнего полного
переноса строки: недописанный хвост остаётся до следующего прохода. Offset
сбрасывается, когда файл пересоздан (сменился inode) или усечён (size меньше
запомненного).

Импорт идемпотентен: ход опознаётся по `message_id`, вызов инструмента — по
`tool_use_id`, оба поля UNIQUE. Это же гасит копии ходов, которые Claude Code
переносит в новый файл при resume.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .parser import ParsedRecord, RecordKind, Usage, parse_line

log = logging.getLogger(__name__)

#: Служебные ответы Claude Code («No response requested», упирание в лимит):
#: нулевой usage и отсутствующий requestId, расходом не являются.
SYNTHETIC_MODEL = "<synthetic>"


@dataclass
class IngestStats:
    """Что дал проход по одному файлу."""

    path: str
    lines: int = 0
    turns_new: int = 0
    turns_known: int = 0  # уже были в БД: повтор хвоста или копия при resume
    tools_new: int = 0
    prompts: int = 0
    unknown: int = 0
    sessions: int = 0
    offset: int = 0
    restarted: bool = False  # offset сбрасывался: файл пересоздан или усечён


@dataclass
class _Turn:
    """Ход, собираемый из нескольких записей с одним `message_id`."""

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
    """Метаданные сессии, накопленные по её записям в файле."""

    session_id: str
    started_at: str | None = None
    last_at: str | None = None
    first_prompt: str | None = None
    cwd: str | None = None
    last_record_kind: str | None = None  # чем сессия заканчивается на этой записи
    last_record_at: str | None = None
    title: str | None = None
    title_source: str | None = None


def ingest_file(conn: sqlite3.Connection, path: Path) -> IngestStats:
    """Дочитать файл транскрипта с сохранённого offset и записать в БД."""
    stats = IngestStats(path=str(path))
    try:
        file_stat = path.stat()
    except OSError as exc:
        log.warning("файл транскрипта недоступен (%s): %s", path, exc)
        return stats

    offset, stats.restarted = _resume_offset(conn, path, file_stat.st_ino, file_stat.st_size)
    chunk, new_offset = _read_complete_lines(path, offset)
    stats.offset = new_offset

    turns: dict[str, _Turn] = {}
    sessions: dict[str, _Session] = {}
    for raw in chunk:
        stats.lines += 1
        record = parse_line(raw)
        if record is None:
            continue
        _collect(record, turns, sessions, stats)

    with conn:  # одна транзакция на файл: либо файл учтён, либо offset не сдвинут
        project_id = _upsert_project(conn, path, sessions)
        _upsert_sessions(conn, sessions, project_id)
        _insert_turns(conn, turns, stats)
        _refresh_session_totals(conn, sessions.keys())
        _save_offset(
            conn, path, file_stat.st_ino, file_stat.st_size, new_offset, file_stat.st_mtime
        )

    stats.sessions = len(sessions)
    return stats


def ingest_tree(conn: sqlite3.Connection, root: Path) -> list[IngestStats]:
    """Обойти каталог транскриптов целиком (полная версия — задача B2)."""
    return [ingest_file(conn, path) for path in sorted(root.rglob("*.jsonl"))]


# --- чтение хвоста ----------------------------------------------------------


def _resume_offset(conn: sqlite3.Connection, path: Path, inode: int, size: int) -> tuple[int, bool]:
    row = conn.execute(
        "SELECT inode, size, offset FROM files WHERE path = ?", (str(path),)
    ).fetchone()
    if row is None:
        return 0, False
    if row["inode"] != inode or size < row["size"]:
        log.info("файл пересоздан или усечён, читаем сначала: %s", path)
        return 0, True
    return int(row["offset"]), False


def _read_complete_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Прочитать от offset только полные строки; вернуть их и новый offset.

    Недописанная последняя строка (без перевода строки) не отдаётся и не
    сдвигает offset — Claude Code допишет её в следующий момент.
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


# --- накопление -------------------------------------------------------------


def _collect(
    record: ParsedRecord,
    turns: dict[str, _Turn],
    sessions: dict[str, _Session],
    stats: IngestStats,
) -> None:
    if record.kind is RecordKind.TITLE and record.session_id:
        # У записей названия нет ни времени, ни uuid — только sessionId.
        session = sessions.setdefault(record.session_id, _Session(session_id=record.session_id))
        if record.title_source == "custom" or session.title_source != "custom":
            session.title = record.title
            session.title_source = record.title_source
        return
    if record.session_id and record.ts:
        _touch_session(sessions, record)

    if record.kind is RecordKind.PROMPT:
        stats.prompts += 1
        return
    if record.kind is RecordKind.UNKNOWN:
        stats.unknown += 1  # складывание в raw_events — задача B6
        return
    if record.kind is not RecordKind.ASSISTANT:
        return
    if not (record.message_id and record.session_id and record.ts):
        return
    if record.model == SYNTHETIC_MODEL:
        return

    turn = turns.get(record.message_id)
    if turn is not None:
        # Записи одного хода несут расход неравномерно: у части он ещё нулевой.
        # Правильное значение — поэлементный максимум, см. Usage.merge.
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
    # Служебные записи (attachment и прочие) на «чем закончилась сессия» не
    # влияют: между промптом и ответом их пишется сколько угодно.
    if record.kind is not RecordKind.UNKNOWN and (
        session.last_record_at is None or record.ts >= session.last_record_at
    ):
        session.last_record_kind = record.kind.value
        session.last_record_at = record.ts
    session.cwd = session.cwd or record.cwd
    # Подпись сессии — первый настоящий промпт человека: ни сабагент,
    # ни пересказ автосуммаризации на эту роль не годятся.
    if (
        session.first_prompt is None
        and record.kind is RecordKind.PROMPT
        and record.prompt_text
        and not record.is_sidechain
        and not record.is_compact_summary
    ):
        session.first_prompt = record.prompt_text


# --- запись -----------------------------------------------------------------


def _upsert_project(
    conn: sqlite3.Connection, path: Path, sessions: dict[str, _Session]
) -> int | None:
    slug = path.parent.name
    if not slug:
        return None
    root_path = next((s.cwd for s in sessions.values() if s.cwd), None)
    conn.execute(
        """
        INSERT INTO projects (slug, root_path) VALUES (?, ?)
        ON CONFLICT(slug) DO UPDATE SET root_path = COALESCE(projects.root_path, excluded.root_path)
        """,
        (slug, root_path),
    )
    row = conn.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
    return int(row["id"]) if row else None


def _upsert_sessions(
    conn: sqlite3.Connection, sessions: dict[str, _Session], project_id: int | None
) -> None:
    conn.executemany(
        """
        INSERT INTO sessions (id, project_id, started_at, last_at, first_prompt,
                              last_record_kind, last_record_at, title, title_source)
        VALUES (:id, :project_id, :started_at, :last_at, :first_prompt,
                :last_record_kind, :last_record_at, :title, :title_source)
        ON CONFLICT(id) DO UPDATE SET
            project_id   = COALESCE(sessions.project_id, excluded.project_id),
            started_at   = MIN(COALESCE(sessions.started_at, excluded.started_at),
                               excluded.started_at),
            last_at      = MAX(COALESCE(sessions.last_at, excluded.last_at), excluded.last_at),
            first_prompt = COALESCE(sessions.first_prompt, excluded.first_prompt),
            last_record_kind = CASE
                WHEN excluded.last_record_at >= COALESCE(sessions.last_record_at, '')
                THEN excluded.last_record_kind ELSE sessions.last_record_kind END,
            last_record_at = MAX(COALESCE(sessions.last_record_at, ''), excluded.last_record_at),
            -- Название, заданное человеком, не затирается сгенерированным.
            title = CASE
                WHEN excluded.title IS NULL THEN sessions.title
                WHEN sessions.title_source = 'custom' AND excluded.title_source <> 'custom'
                THEN sessions.title ELSE excluded.title END,
            title_source = CASE
                WHEN excluded.title_source IS NULL THEN sessions.title_source
                WHEN sessions.title_source = 'custom' AND excluded.title_source <> 'custom'
                THEN sessions.title_source ELSE excluded.title_source END
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
        "INSERT OR IGNORE INTO tool_calls (turn_id, tool_use_id, tool, detail) VALUES (?, ?, ?, ?)",
        rows,
    )
    stats.tools_new = _count(conn, "tool_calls") - tools_before


def _refresh_session_totals(conn: sqlite3.Connection, session_ids: Iterable[str]) -> None:
    """Пересчитать агрегаты затронутых сессий (тяжёлое считается в SQL)."""
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
