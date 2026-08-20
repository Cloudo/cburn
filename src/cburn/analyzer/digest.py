"""The period digest without an LLM (task D1, SPEC §6).

This is the advisor's input: everything it has to know about the period is collected
beforehand and computed in SQL. The model gets JSON, not transcripts.

Privacy (SPEC §7, a project invariant). Only these things reach the digest:

* numbers - tokens, cost, counters, shares;
* tool names and normalised bash commands ("first word + subcommand",
  arguments were dropped back at parse time);
* session ids and project names;
* sizes of instruction files, but not their contents.

Neither conversation text, nor prompts, nor session titles are here: `ai-title`
retells the conversation, so it is content too. Command snippets are included only
under the `analyzer.allow_snippets` flag - as of today nobody switches it on.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import metrics, paths

#: The digest ceiling from the spec: 20k tokens. We count roughly - 4 characters per
#: token; an exact count would cost an API call, and an order of magnitude is enough.
TOKEN_LIMIT = 20_000
CHARS_PER_TOKEN = 4

#: How many rows to keep in each list. Beyond that the tail carries no information
#: while eating the budget just fine.
TOP_COMMANDS = 20
TOP_SESSIONS = 10
TOP_TOOLS = 12

#: The life of the five-minute cache. A write that no turn reached within it is money
#: spent on nothing - the same context is written again on the next turn.
CACHE_TTL_SECONDS = 300

#: Tools that decide nothing on their own: reading and searching. A turn that held
#: only those is mechanical work, and Opus is overkill for it (SPEC §6).
MECHANICAL_TOOLS = {"Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite"}


def build(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    *,
    config: dict[str, Any] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Build the period digest. Returns a JSON-compatible dict."""
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
        "cache": _cache(conn, since, until, project),
        "compaction": _compaction(conn, since, project),
        "mcp": _mcp(conn, since, project),
        "permissions": _permissions(conn, since, until, project),
        "off_transcript": _off_transcript(conn, since, until, project),
        "instructions": _instructions(),
    }
    digest["size"] = _size(digest)
    return digest


def _tools(conn: sqlite3.Connection, since: datetime, project: str | None) -> dict[str, Any]:
    """The tool profile and the top normalised commands.

    A heredoc shows up as a row of its own (`python3 <<`): the same script driven
    through ten times is a noticeable spend, yet by command name it is
    indistinguishable from an ordinary call.
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


def _cache(
    conn: sqlite3.Connection, since: datetime, until: datetime | None, project: str | None
) -> dict[str, Any]:
    """Writes into the five-minute cache that could not have been read back.

    The five-minute cache is paid for on the way in and pays for itself on the way out -
    but only if the next turn comes within its five minutes. A pause longer than that and
    the money is spent on nothing: the same context is written again on the next turn.
    Which bytes were re-read is not in the transcript, but the deadline is a fact, and a
    gap wider than it is enough to call a write wasted.

    The tail of the period is left alone: a write from a minute ago is not wasted, its
    turn has simply not come yet.
    """
    clause, params = metrics.project_filter(project, "session_id")
    edge = (until or datetime.now(UTC)) - timedelta(seconds=CACHE_TTL_SECONDS)
    rows = conn.execute(
        f"""
        WITH ordered AS (
            SELECT session_id, ts, cache_write_5m, cache_write_1h, cache_read,
                   LEAD(ts) OVER (PARTITION BY session_id ORDER BY ts) AS next_ts
              FROM turns
             WHERE ts >= ?{clause}
        )
        SELECT SUM(cache_write_5m) AS write_5m,
               SUM(cache_write_1h) AS write_1h,
               SUM(cache_read)     AS read,
               SUM(CASE WHEN wasted THEN cache_write_5m ELSE 0 END) AS expired,
               SUM(CASE WHEN wasted AND cache_write_5m > 0 THEN 1 ELSE 0 END) AS pauses
          FROM (SELECT *,
                       (next_ts IS NULL AND ts < ?)
                    OR (next_ts IS NOT NULL
                        AND (julianday(next_ts) - julianday(ts)) * 86400 > ?) AS wasted
                  FROM ordered)
        """,  # noqa: S608
        (
            metrics._utc_stamp(since),
            *params,
            metrics._utc_stamp(edge),
            CACHE_TTL_SECONDS,
        ),
    ).fetchone()

    write_5m = int(rows["write_5m"] or 0)
    expired = int(rows["expired"] or 0)
    return {
        "write_5m": write_5m,
        "write_1h": int(rows["write_1h"] or 0),
        "read": int(rows["read"] or 0),
        "expired_5m": expired,
        "expired_share": round(expired / write_5m, 3) if write_5m else 0.0,
        "pauses": int(rows["pauses"] or 0),
    }


def _compaction(conn: sqlite3.Connection, since: datetime, project: str | None) -> dict[str, Any]:
    """Auto-compactions and what the first turn after each of them cost.

    Compaction itself is normal work and no reason for advice - Claude Code does it to
    keep going at all. What costs money is the turn right after: the summary is read back
    at full price, and part of the working thread is gone with it. So the number worth
    showing is not "it happened", but what it came to - and the cure is to cut the session
    before the ceiling, not to forbid the compaction.
    """
    clause, params = metrics.project_filter(project, "e.session_id")
    rows = conn.execute(
        f"""
        SELECT e.session_id AS session_id,
               s.title      AS title,
               t.cost_usd   AS cost_usd,
               t.cache_read AS cache_read
          FROM session_events AS e
          JOIN sessions AS s ON s.id = e.session_id
          LEFT JOIN turns AS t
            ON t.id = (SELECT id FROM turns
                        WHERE session_id = e.session_id AND ts >= e.ts
                        ORDER BY ts LIMIT 1)
         WHERE e.kind = 'compact' AND e.ts >= ?{clause}
        """,  # noqa: S608
        (metrics._utc_stamp(since), *params),
    ).fetchall()

    by_session: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = by_session.setdefault(
            row["session_id"],
            {
                "session_id": row["session_id"],
                "title": row["title"],
                "events": 0,
                "cost_after_usd": 0.0,
                "read_after": 0,
            },
        )
        entry["events"] += 1
        entry["cost_after_usd"] += float(row["cost_usd"] or 0.0)
        entry["read_after"] += int(row["cache_read"] or 0)

    top = sorted(by_session.values(), key=lambda item: item["cost_after_usd"], reverse=True)
    for entry in top:
        entry["cost_after_usd"] = round(entry["cost_after_usd"], 4)
    return {
        "events": len(rows),
        "sessions": len(by_session),
        "cost_after_usd": round(sum(item["cost_after_usd"] for item in top), 4),
        "read_after": sum(item["read_after"] for item in top),
        "top": top[:TOP_SESSIONS],
    }


def _heavy_sessions(
    conn: sqlite3.Connection, since: datetime, context_crit: int, project: str | None
) -> list[dict]:
    """Sessions worth showing to the advisor: expensive and bloated ones.

    There are no titles here on purpose: `ai-title` is a retelling of the conversation.
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
         -- Without prices the cost is zero for everyone, and a single ordering by it
         -- would give a random list: then we sort by volume.
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
    """Work lines: one task carried through resume several times over.

    From the manual report: 87% of the spend went into two unclosed lines - and seeing
    that is only possible once the whole chain is assembled.
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
    """The share of Opus on turns that held only reading and searching.

    A direct candidate for advice: a simpler model handles such work.
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
    """MCP servers: how many times each one was actually called during the period.

    Servers that are connected but never called are a spend too: their descriptions hang
    in every request. The list of connected ones lives in the Claude Code configs, and we
    will not read those here - the call count already answers the question.
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
    # What the mere fact of connecting costs is known only to telemetry: a server
    # starts anew in every session, even if it is never called.
    connections = metrics.otel_mcp(conn, since, project=project)
    if connections["servers"]:
        profile["connections"] = connections
    # Where the servers come from: a plugin drags MCP, skills and commands along,
    # and all of it loads in every session whether it is needed or not.
    plugins = metrics.otel_plugins(conn, since, project=project)
    if plugins:
        profile["plugins"] = plugins
    return profile


def _permissions(
    conn: sqlite3.Connection, since: datetime, until: datetime | None, project: str | None
) -> dict:
    """Permission confirmations: how many times work stopped waiting for an answer.

    Counted from telemetry - Claude Code writes neither the "allow?" question nor the
    answer to it into the transcript (milestone E). Without telemetry the section is
    marked `available: false`, otherwise the advisor reads zero confirmations as a fact.
    """
    stats = metrics.otel_permissions(conn, since, until, project)
    if not stats["decisions"]:
        return {"available": False, "note": "OTel telemetry is not switched on - there is no data"}
    return {"available": True, **stats}


def _off_transcript(
    conn: sqlite3.Connection, since: datetime, until: datetime | None, project: str | None
) -> dict:
    """The spend of service requests that are absent from transcripts (milestone E).

    The advisor needs this so it does not explain a number mismatch by chance:
    the other digest sections are counted from transcripts and are understated
    by exactly this much.
    """
    usage = metrics.otel_usage(conn, since, until, project)
    work = metrics.otel_work(conn, since, until, project)
    prompts = metrics.otel_prompts(conn, since, until, project)
    hooks = metrics.otel_hooks(conn, since, until, project)
    if not any(
        (
            usage["tokens"],
            usage["cost_usd"],
            work["active_seconds"],
            prompts["prompts"],
            hooks["seconds"],
        )
    ):
        return {"available": False, "note": "OTel telemetry is not switched on - there is no data"}
    return {
        "available": True,
        "tokens": usage["tokens"],
        "cost_usd": usage["cost_usd"],
        "share_of_cost": round(usage["share"], 4),
        "kinds": usage["request_kinds"],
        # How long work actually ran and what came out of it: the spend is neither
        # good nor bad on its own - what matters is what was done for it.
        "active_minutes": round(work["active_seconds"] / 60, 1),
        "lines_added": work["lines_added"],
        "lines_removed": work["lines_removed"],
        # Slash commands: in the transcript only markup blocks remain of them,
        # and the parser does not unpack those - telemetry names the command directly.
        "prompts": prompts,
        # Hooks run between turns, and all that remains of them in the transcript
        # is a pause: an HTTP hook to an unreachable service eats tens of seconds
        # on every prompt, and from the history files that is indistinguishable.
        "hooks": hooks,
    }


def _instructions() -> dict:
    """The size of the persistent instructions: they ride along with every request.

    Bytes and a token estimate are counted, the contents are never read out - but
    knowing the size of what you pay for on every turn is useful.
    """
    files = []
    for path in (paths.CLAUDE_MD,):
        if not path.is_file():
            continue
        size = path.stat().st_size
        files.append({"path": str(path), "bytes": size, "tokens_approx": size // CHARS_PER_TOKEN})
    return {"files": files, "bytes": sum(item["bytes"] for item in files)}


def _size(digest: dict[str, Any]) -> dict[str, Any]:
    """What the digest will cost the advisor."""
    text = json.dumps(digest, ensure_ascii=False)
    tokens = len(text) // CHARS_PER_TOKEN
    return {
        "chars": len(text),
        "tokens_approx": tokens,
        "limit": TOKEN_LIMIT,
        "within_limit": tokens <= TOKEN_LIMIT,
    }
