"""Дайджест периода без LLM (задача D1, ТЗ §6).

Это вход советчика: всё, что он должен знать о периоде, собрано заранее и
посчитано в SQL. Модель получает JSON, а не транскрипты.

Приватность (ТЗ §7, инвариант проекта). В дайджест попадают только:

* числа — токены, стоимость, счётчики, доли;
* имена инструментов и нормализованные bash-команды («первое слово +
  подкоманда», аргументы отброшены ещё при разборе);
* идентификаторы сессий и имена проектов;
* размеры файлов инструкций, но не их содержимое.

Ни текста переписки, ни промптов, ни названий сессий здесь нет: `ai-title`
пересказывает разговор, значит это тоже содержимое. Фрагменты команд включаются
только под флагом `analyzer.allow_snippets` — на сегодня их не включает никто.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .. import metrics, paths

#: Потолок дайджеста из ТЗ: 20k токенов. Считаем грубо — 4 символа на токен;
#: точный счёт стоил бы обращения к API, а нужен порядок величины.
TOKEN_LIMIT = 20_000
CHARS_PER_TOKEN = 4

#: Сколько строк держать в каждом списке. Дальше хвост не несёт информации,
#: но исправно ест бюджет.
TOP_COMMANDS = 20
TOP_SESSIONS = 10
TOP_TOOLS = 12

#: Инструменты, которые сами по себе ничего не решают: чтение и поиск. Ход,
#: где были только они, — механическая работа, и Opus на ней избыточен (ТЗ §6).
MECHANICAL_TOOLS = {"Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite"}


def build(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    *,
    config: dict[str, Any] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Собрать дайджест периода. Возвращает JSON-совместимый словарь."""
    settings = config or {}
    thresholds = settings.get("thresholds") or {}
    context_crit = int(thresholds.get("context_crit") or 150_000)

    digest: dict[str, Any] = {
        "period": {
            "since": since.astimezone(UTC).isoformat(),
            "until": (until or datetime.now(UTC)).astimezone(UTC).isoformat(),
            "project": project,
        },
        "usage": metrics.window_usage(conn, since, until, project=project),
        "models": metrics.model_share(conn, since, project=project),
        "idle": metrics.idle_turns(conn, since, project=project),
        "tools": _tools(conn, since, project),
        "sessions": _heavy_sessions(conn, since, context_crit, project),
        "chains": _chains(conn, since, project),
        "mechanical_opus": _mechanical_opus(conn, since, project),
        "mcp": _mcp(conn, since, project),
        "permissions": _permissions(conn, since, until, project),
        "off_transcript": _off_transcript(conn, since, until, project),
        "instructions": _instructions(),
    }
    digest["size"] = _size(digest)
    return digest


def _tools(conn: sqlite3.Connection, since: datetime, project: str | None) -> dict[str, Any]:
    """Профиль инструментов и топ нормализованных команд.

    Heredoc виден отдельной строкой (`python3 <<`): один и тот же скрипт,
    прогнанный десять раз, — заметный расход, а по имени команды он
    неотличим от обычного вызова.
    """
    profile = metrics.tool_profile(conn, since, limit=TOP_COMMANDS, project=project)
    return {
        "calls": profile["tools_total"],
        "by_tool": profile["tools"][:TOP_TOOLS],
        "bash_commands": profile["bash_commands"],
        "heredoc_calls": sum(
            row["calls"] for row in profile["bash_commands"] if row["command"].endswith("<<")
        ),
    }


def _heavy_sessions(
    conn: sqlite3.Connection, since: datetime, context_crit: int, project: str | None
) -> list[dict]:
    """Сессии, которые стоит показать советчику: дорогие и раздутые.

    Названий здесь нет намеренно: `ai-title` — пересказ разговора.
    """
    clause, params = metrics.project_filter(project, "s.id")
    rows = conn.execute(
        f"""
        SELECT s.id, COALESCE(p.display_name, p.slug) AS project, s.last_context,
               s.parent_session_id,
               COUNT(t.id)                                    AS turns,
               COALESCE(SUM(t.output_tokens), 0)              AS output_tokens,
               COALESCE(SUM(t.cache_read), 0)                 AS cache_read,
               COALESCE(SUM(t.cost_usd), 0)                   AS cost_usd,
               COALESCE(SUM(t.is_sidechain), 0)               AS sidechain_turns,
               SUM(t.output_tokens < 10 AND t.context_estimate > 50000) AS idle_turns
          FROM sessions AS s
          LEFT JOIN projects AS p ON p.id = s.project_id
          JOIN turns AS t ON t.session_id = s.id AND t.ts >= ?
         WHERE s.hidden = 0{clause}
         GROUP BY s.id
         -- Без цен стоимость у всех нулевая, и один порядок по ней дал бы
         -- случайный список: тогда сортируем по объёму.
         ORDER BY cost_usd DESC, (SUM(t.cache_read) + SUM(t.output_tokens)) DESC
         LIMIT ?
        """,  # noqa: S608
        (metrics._utc_stamp(since), *params, TOP_SESSIONS),
    )
    sessions = [dict(row) for row in rows]
    for session in sessions:
        session["over_context_limit"] = session["last_context"] > context_crit
    return sessions


def _chains(conn: sqlite3.Connection, since: datetime, project: str | None) -> list[dict]:
    """Линии работы: одна задача, продолженная через resume несколько раз.

    Из ручного отчёта: 87% расхода ушло в две незакрытые линии — и увидеть это
    можно только собрав цепочку целиком.
    """
    clause, params = metrics.project_filter(project, "s.id")
    rows = conn.execute(
        f"""
        SELECT s.parent_session_id AS root, COUNT(*) AS continuations
          FROM sessions AS s
         WHERE s.parent_session_id IS NOT NULL AND s.last_at >= ?{clause}
         GROUP BY s.parent_session_id
         ORDER BY continuations DESC
         LIMIT ?
        """,  # noqa: S608
        (metrics._utc_stamp(since), *params, TOP_SESSIONS),
    ).fetchall()
    chains = []
    for row in rows:
        chain = metrics.session_chain(conn, row["root"])
        chains.append(
            {
                "root": row["root"],
                "sessions": len(chain["sessions"]),
                "turns": chain["turns"],
                "tokens": chain["tokens"],
                "cost_usd": chain["cost_usd"],
            }
        )
    return chains


def _mechanical_opus(conn: sqlite3.Connection, since: datetime, project: str | None) -> dict:
    """Доля Opus на ходах, где были только чтение и поиск.

    Прямой кандидат в советы: такую работу тянет модель попроще.
    """
    clause, params = metrics.project_filter(project, "t.session_id")
    placeholders = ",".join("?" * len(MECHANICAL_TOOLS))
    row = conn.execute(
        f"""
        WITH mechanical AS (
            SELECT t.id, t.model, t.cost_usd
              FROM turns AS t
              JOIN tool_calls AS c ON c.turn_id = t.id
             WHERE t.ts >= ?{clause}
             GROUP BY t.id
            HAVING SUM(c.tool NOT IN ({placeholders})) = 0
        )
        SELECT COUNT(*)                                             AS turns,
               COALESCE(SUM(model LIKE '%opus%'), 0)                AS opus_turns,
               COALESCE(SUM(CASE WHEN model LIKE '%opus%' THEN cost_usd ELSE 0 END), 0)
                                                                    AS opus_cost_usd
          FROM mechanical
        """,  # noqa: S608
        (metrics._utc_stamp(since), *params, *sorted(MECHANICAL_TOOLS)),
    ).fetchone()
    result = dict(row)
    result["share"] = result["opus_turns"] / result["turns"] if result["turns"] else 0.0
    return result


def _mcp(conn: sqlite3.Connection, since: datetime, project: str | None) -> dict:
    """MCP-серверы: сколько раз каждый реально позвали за период.

    Подключённые, но ни разу не позванные серверы — тоже расход: их описания
    висят в каждом запросе. Список подключённых лежит в конфигах Claude Code,
    и читать их здесь мы не будем — счёт вызовов уже отвечает на вопрос.
    """
    clause, params = metrics.project_filter(project, "t.session_id")
    rows = conn.execute(
        f"""
        SELECT c.tool, COUNT(*) AS calls
          FROM tool_calls AS c JOIN turns AS t ON t.id = c.turn_id
         WHERE t.ts >= ? AND c.tool LIKE 'mcp\\_\\_%' ESCAPE '\\'{clause}
         GROUP BY c.tool
        """,  # noqa: S608
        (metrics._utc_stamp(since), *params),
    )
    servers: dict[str, int] = {}
    for row in rows:
        parts = row["tool"].removeprefix("mcp__").split("__")
        server = parts[0] if parts else row["tool"]
        servers[server] = servers.get(server, 0) + row["calls"]
    profile = {
        "servers": [
            {"server": name, "calls": calls}
            for name, calls in sorted(servers.items(), key=lambda item: -item[1])
        ],
        "calls": sum(servers.values()),
    }
    # Сколько стоит сам факт подключения, знает только телеметрия: сервер
    # стартует заново в каждой сессии, даже если его ни разу не позвали.
    connections = metrics.otel_mcp(conn, since, project=project)
    if connections["servers"]:
        profile["connections"] = connections
    return profile


def _permissions(
    conn: sqlite3.Connection, since: datetime, until: datetime | None, project: str | None
) -> dict:
    """Подтверждения разрешений: сколько раз работа останавливалась ради ответа.

    Считается по телеметрии — в транскрипт Claude Code не пишет ни вопрос
    «разрешить?», ни ответ на него (веха E). Без телеметрии секция помечена
    `available: false`, иначе советчик прочтёт ноль подтверждений как факт.
    """
    stats = metrics.otel_permissions(conn, since, until, project)
    if not stats["decisions"]:
        return {"available": False, "note": "телеметрия OTel не включена — данных нет"}
    return {"available": True, **stats}


def _off_transcript(
    conn: sqlite3.Connection, since: datetime, until: datetime | None, project: str | None
) -> dict:
    """Расход служебных запросов, которых нет в транскриптах (веха E).

    Советчику это нужно, чтобы не объяснять расхождение цифр случайностью:
    остальные разделы дайджеста считаны по транскриптам и на эту величину
    занижены.
    """
    usage = metrics.otel_usage(conn, since, until, project)
    work = metrics.otel_work(conn, since)
    if not usage["tokens"] and not usage["cost_usd"] and not work["active_seconds"]:
        return {"available": False, "note": "телеметрия OTel не включена — данных нет"}
    return {
        "available": True,
        "tokens": usage["tokens"],
        "cost_usd": usage["cost_usd"],
        "share_of_cost": round(usage["share"], 4),
        "kinds": usage["request_kinds"],
        # Сколько времени работа реально шла и что получилось на выходе:
        # расход сам по себе ни хорош, ни плох — важно, что за него сделано.
        "active_minutes": round(work["active_seconds"] / 60, 1),
        "lines_added": work["lines_added"],
        "lines_removed": work["lines_removed"],
    }


def _instructions() -> dict:
    """Размер постоянных инструкций: они едут в каждый запрос.

    Считаются байты и оценка токенов, содержимое не читается наружу — но
    объём того, что вы платите за каждый ход, знать полезно.
    """
    files = []
    for path in (paths.CLAUDE_MD,):
        if not path.is_file():
            continue
        size = path.stat().st_size
        files.append({"path": str(path), "bytes": size, "tokens_approx": size // CHARS_PER_TOKEN})
    return {"files": files, "bytes": sum(item["bytes"] for item in files)}


def _size(digest: dict[str, Any]) -> dict[str, Any]:
    """Во что дайджест обойдётся советчику."""
    text = json.dumps(digest, ensure_ascii=False)
    tokens = len(text) // CHARS_PER_TOKEN
    return {
        "chars": len(text),
        "tokens_approx": tokens,
        "limit": TOKEN_LIMIT,
        "within_limit": tokens <= TOKEN_LIMIT,
    }
