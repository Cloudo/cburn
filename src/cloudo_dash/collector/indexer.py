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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import paths
from ..pricing import apply_costs
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
    last_prompt: str | None = None
    last_stop_reason: str | None = None
    last_turn_at: str | None = None  # время хода, чей stop_reason запомнен


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
    unknown: list[tuple[int, ParsedRecord, str]] = []
    events: list[tuple[str, str, str]] = []
    for line_no, raw in enumerate(chunk, start=1):
        stats.lines += 1
        record = parse_line(raw)
        if record is None:
            continue
        if record.kind is RecordKind.UNKNOWN:
            unknown.append((line_no, record, raw))
        # Автосуммаризация: после неё контекст обваливается, и на графике
        # должно быть видно, почему (задача C2).
        if record.is_compact_summary and record.session_id and record.ts:
            events.append((record.session_id, record.ts, "compact"))
        _collect(record, turns, sessions, stats)

    with conn:  # одна транзакция на файл: либо файл учтён, либо offset не сдвинут
        project_id = _upsert_project(conn, path, sessions)
        _upsert_sessions(conn, sessions, project_id)
        _insert_turns(conn, turns, stats)
        _store_unknown(conn, path, unknown)
        _store_events(conn, events)
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
    """Обойти каталог транскриптов целиком (задача B2).

    `on_file(готово, всего, путь)` вызывается после каждого файла — CLI рисует
    по нему прогресс. Отдельная фоновая задача не понадобилась: полный обход
    639 МБ занимает секунды, см. README.
    """
    paths_to_read = sorted(root.rglob("*.jsonl"))
    results = []
    for index, path in enumerate(paths_to_read, start=1):
        results.append(ingest_file(conn, path))
        if on_file is not None:
            on_file(index, len(paths_to_read), path)
    return results


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
    if record.kind is RecordKind.LAST_PROMPT and record.session_id:
        # У записи нет времени: она всегда описывает текущее состояние сессии.
        session = sessions.setdefault(record.session_id, _Session(session_id=record.session_id))
        session.last_prompt = record.prompt_text
        return
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

    known = sessions.get(record.session_id)
    if known is not None and (known.last_turn_at is None or record.ts >= known.last_turn_at):
        known.last_turn_at = record.ts
        known.last_stop_reason = record.stop_reason

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


def _project_slug(path: Path) -> str:
    """Slug проекта — каталог верхнего уровня в каталоге транскриптов.

    Имя родительского каталога тут не годится: транскрипты сабагентов лежат
    в `<проект>/<сессия>/subagents/`, и от них заводился псевдопроект
    «subagents», куда уезжали и сами родительские сессии.
    """
    try:
        parts = path.relative_to(paths.CLAUDE_PROJECTS_DIR).parts
    except ValueError:
        return path.parent.name  # файл вне каталога транскриптов (тесты, ручной прогон)
    return parts[0] if len(parts) > 1 else ""


def project_name(root_path: str | None) -> str | None:
    """Человеческое имя проекта — последний сегмент рабочего пути.

    Slug остаётся ключом каталога в `~/.claude/projects`, но на экране от него
    толку нет: `-Users-cloudo-code-cloudo-dash` читается хуже, чем `cloudo-dash`.
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
            -- Батч из одних служебных записей приходит без last_record_at, и NULL
            -- в скалярном MAX обнулил бы колонку, а с ней и паузу до простоя.
            last_record_kind = CASE
                WHEN excluded.last_record_at IS NULL THEN sessions.last_record_kind
                WHEN excluded.last_record_at >= COALESCE(sessions.last_record_at, '')
                THEN excluded.last_record_kind ELSE sessions.last_record_kind END,
            last_record_at = MAX(COALESCE(sessions.last_record_at, excluded.last_record_at),
                                 COALESCE(excluded.last_record_at, sessions.last_record_at)),
            -- Название, заданное человеком, не затирается сгенерированным.
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
        -- Нормализация команды выводится из данных, а её правила меняются:
        -- при повторном чтении файла деталь обновляется, а не застревает старой.
        ON CONFLICT(tool_use_id) DO UPDATE SET detail = excluded.detail
        """,
        rows,
    )
    stats.tools_new = _count(conn, "tool_calls") - tools_before


#: Сколько полных экземпляров хранить на пару (тип, версия). Дальше — счётчик:
#: незнакомых записей в истории десятки тысяч, и примеров хватает единиц.
RAW_SAMPLE_LIMIT = 5


def _store_unknown(
    conn: sqlite3.Connection, path: Path, records: list[tuple[int, ParsedRecord, str]]
) -> None:
    """Сложить незнакомые записи: примеры и счётчик по паре (тип, версия).

    Формат транскриптов недокументирован и меняется между версиями Claude Code.
    Счётчик отвечает на вопрос «что появилось», примеры — «как оно выглядит»;
    полный payload дальше первых `RAW_SAMPLE_LIMIT` не нужен (задача B6).
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


#: По сколько message_id спрашивать за раз: в файле их бывают тысячи, а число
#: параметров в запросе SQLite ограничено.
_CHUNK = 500


def _store_events(conn: sqlite3.Connection, events: list[tuple[str, str, str]]) -> None:
    """Заметные моменты сессии. Повтор чтения хвоста их не задваивает: UNIQUE."""
    if not events:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO session_events (session_id, ts, kind) VALUES (?, ?, ?)",
        events,
    )


def _link_parents(conn: sqlite3.Connection, turns: dict[str, _Turn]) -> None:
    """Связать сессию с той, из которой её продолжили (задача B5).

    При resume Claude Code копирует прошлые ходы в новый файл с новым
    `sessionId`, сохраняя `message.id`. Дедупликация оставляет такой ход за
    первым владельцем — значит, ходы этого файла, записанные на чужую сессию,
    и есть скопированная история.

    Направление связи задаёт время, а не порядок чтения файлов: родитель — та
    из двух сессий, что началась раньше. Иначе связь зависела бы от того, чей
    файл попался обходу первым. Пара сравнивается по `(started_at, id)`, так
    что порядок строгий и цикл невозможен. Связь ставится один раз.
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
        log.info("сессия %s продолжает %s (общих ходов %s)", younger, older, shared[candidate])


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
