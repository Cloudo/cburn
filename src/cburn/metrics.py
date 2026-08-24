"""Database queries. Heavy aggregates are computed in SQL, not in Python.

For now this holds only what is needed to reconcile the numbers of a session (task A3);
the SPEC §4 metrics - burn rate per window, model share, idle turns - are task B3.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from . import paths


@dataclass(frozen=True)
class SessionSummary:
    """Totals for one session."""

    session_id: str
    project: str | None
    root_path: str | None
    title: str | None
    started_at: str | None
    last_at: str | None
    first_prompt: str | None
    turns: int
    sidechain_turns: int
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int
    cost_usd: float
    sidechain_tokens: int
    sidechain_cost_usd: float
    parent_session_id: str | None
    last_context: int

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h


def session_summary(conn: sqlite3.Connection, session_id: str) -> SessionSummary | None:
    """Session totals. Computed from `turns`, not from the cache in `sessions`."""
    row = conn.execute(
        """
        SELECT s.id                                        AS session_id,
               COALESCE(p.display_name, p.slug)            AS project,
               p.root_path                                 AS root_path,
               s.title                                     AS title,
               s.started_at, s.last_at, s.first_prompt,
               COUNT(t.id)                                 AS turns,
               COALESCE(SUM(t.is_sidechain), 0)            AS sidechain_turns,
               COALESCE(SUM(t.input_tokens), 0)            AS input_tokens,
               COALESCE(SUM(t.output_tokens), 0)           AS output_tokens,
               COALESCE(SUM(t.cache_read), 0)              AS cache_read,
               COALESCE(SUM(t.cache_write_5m), 0)          AS cache_write_5m,
               COALESCE(SUM(t.cache_write_1h), 0)          AS cache_write_1h,
               COALESCE(SUM(t.cost_usd), 0)                AS cost_usd,
               -- Subagent spend belongs to the session and shows as its own row (SPEC §4).
               COALESCE(SUM(t.is_sidechain * (t.input_tokens + t.output_tokens + t.cache_read
                          + t.cache_write_5m + t.cache_write_1h)), 0) AS sidechain_tokens,
               COALESCE(SUM(t.is_sidechain * t.cost_usd), 0) AS sidechain_cost_usd,
               s.parent_session_id,
               s.last_context
          FROM sessions AS s
          LEFT JOIN projects AS p ON p.id = s.project_id
          LEFT JOIN turns    AS t ON t.session_id = s.id
         WHERE s.id = ?
         GROUP BY s.id
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return SessionSummary(**dict(row))


def session_models(conn: sqlite3.Connection, session_id: str) -> list[tuple[str, int, int]]:
    """Distribution of turns and output tokens across models."""
    return [
        (row["model"] or "-", row["turns"], row["output_tokens"])
        for row in conn.execute(
            """
            SELECT model, COUNT(*) AS turns, SUM(output_tokens) AS output_tokens
              FROM turns WHERE session_id = ?
             GROUP BY model ORDER BY output_tokens DESC
            """,
            (session_id,),
        )
    ]


def session_tools(
    conn: sqlite3.Connection, session_id: str, limit: int = 10
) -> list[tuple[str, int]]:
    """The session tool profile: the Bash breakdown is task B3."""
    return [
        (row["tool"], row["calls"])
        for row in conn.execute(
            """
            SELECT c.tool, COUNT(*) AS calls
              FROM tool_calls AS c JOIN turns AS t ON t.id = c.turn_id
             WHERE t.session_id = ?
             GROUP BY c.tool ORDER BY calls DESC LIMIT ?
            """,
            (session_id, limit),
        )
    ]


def session_tool_times(conn: sqlite3.Connection, session_id: str, limit: int = 10) -> list[dict]:
    """How much time the session spent inside each tool (milestone E).

    Only telemetry knows the duration: in the transcript there is nothing between a tool
    request and its result except two timestamps, and those include waiting for a
    permission. `duration_ms` arrives as a string, hence the CAST.
    """
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT json_extract(attrs, '$.tool_name')                    AS tool,
                   COUNT(*)                                              AS calls,
                   SUM(CAST(json_extract(attrs, '$.duration_ms') AS REAL)) / 1000.0 AS seconds,
                   MAX(CAST(json_extract(attrs, '$.duration_ms') AS REAL)) / 1000.0 AS slowest,
                   -- The `success` attribute arrives as a string, and some events lack
                   -- it entirely: without COALESCE the sum would collapse into NULL.
                   SUM(COALESCE(json_extract(attrs, '$.success') IN ('false', 0), 0)) AS failures
              FROM otel_events
             WHERE name = 'tool_result' AND session_id = ?
             GROUP BY tool ORDER BY seconds DESC LIMIT ?
            """,
            (session_id, limit),
        )
    ]


def recent_sessions(
    conn: sqlite3.Connection,
    limit: int = 20,
    project: str | None = None,
    since: datetime | None = None,
) -> list[sqlite3.Row]:
    """The latest sessions by activity, with project and period filters (B7)."""
    clause = ""
    params: list[Any] = []
    if project:
        clause += " AND p.slug LIKE ?"
        params.append(f"%{project}%")
    if since is not None:
        clause += " AND s.last_at >= ?"
        params.append(_utc_stamp(since))
    return list(
        conn.execute(
            f"""
            SELECT s.id, COALESCE(p.display_name, p.slug) AS project, s.last_at, s.started_at,
                   s.turns, s.tokens_out,
                   s.cache_read, s.cache_write, s.cost_usd, s.last_context, s.first_prompt,
                   s.title, s.title_source
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE s.hidden = 0{clause}
             ORDER BY s.last_at DESC LIMIT ?
            """,  # noqa: S608
            (*params, limit),
        )
    )


def period_start(period: str | None) -> datetime | None:
    """The start of a period: `today`, `24h`, `7d`, `all`, a date. None means all history.

    It lives here, not in the CLI: the "Sessions" screen uses the same parsing.
    """
    value = (period or "all").strip().lower()
    now = datetime.now(UTC)
    if value in {"all", ""}:
        return None
    if value == "today":
        return local_day_start(now)
    if value.endswith(("h", "d")) and value[:-1].isdigit():
        hours = int(value[:-1]) * (24 if value.endswith("d") else 1)
        return now - timedelta(hours=hours)
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def known_projects(conn: sqlite3.Connection) -> list[dict]:
    """Projects with a session count - for the filter dropdown."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT p.slug, COALESCE(p.display_name, p.slug) AS name, p.root_path,
                   COUNT(s.id) AS sessions
              FROM projects AS p
              LEFT JOIN sessions AS s ON s.project_id = p.id AND s.hidden = 0
             GROUP BY p.id
             HAVING sessions > 0
             ORDER BY sessions DESC
            """
        )
    ]


def session_turns(conn: sqlite3.Connection, session_id: str, limit: int = 500) -> list[dict]:
    """Session turns in order - for the context chart and the feed (task C2).

    An idle turn is decided by the same threshold as in the summary (SPEC §6): a short
    answer on a large context. The flag is computed in the query rather than stored,
    so that changing the threshold needs no reindex.
    """
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.message_id, t.ts, t.model, t.output_tokens, t.input_tokens,
                   t.cache_read, t.cache_write_5m + t.cache_write_1h AS cache_write,
                   t.context_estimate, t.cost_usd, t.is_sidechain,
                   (t.output_tokens < :max_output
                    AND t.context_estimate > :min_context) AS is_idle,
                   (SELECT GROUP_CONCAT(c.tool, ' ') FROM tool_calls AS c
                     WHERE c.turn_id = t.id) AS tools
              FROM turns AS t
             WHERE t.session_id = :session
             ORDER BY t.ts, t.id
             LIMIT :limit
            """,
            {
                "session": session_id,
                "limit": limit,
                "max_output": IDLE_MAX_OUTPUT,
                "min_context": IDLE_MIN_CONTEXT,
            },
        )
    ]


def session_events(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Notable moments of a session: auto-compactions and resume branch points.

    A fork is not a record in the transcript but a link between sessions, so it is
    assembled from `parent_session_id`, not from `session_events`.
    """
    marks = [
        dict(row)
        for row in conn.execute(
            "SELECT ts, kind FROM session_events WHERE session_id = ? ORDER BY ts",
            (session_id,),
        )
    ]
    marks += [
        {"ts": row["started_at"], "kind": "fork", "session_id": row["id"]}
        for row in conn.execute(
            "SELECT id, started_at FROM sessions WHERE parent_session_id = ? ORDER BY started_at",
            (session_id,),
        )
        if row["started_at"]
    ]
    return sorted(marks, key=lambda mark: mark["ts"] or "")


#: Tip statuses: new, accepted, dismissed (task D6).
ADVICE_STATUSES = ("new", "accepted", "rejected")


def advice_history(
    conn: sqlite3.Connection, limit: int = 20, now: datetime | None = None
) -> list[dict]:
    """Analysis history with nested tips (the "Advice" screen, task D6)."""
    runs = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, ts, kind, period_start, period_end, model, cost_usd, max_severity
              FROM advice ORDER BY ts DESC LIMIT ?
            """,
            (limit,),
        )
    ]
    if not runs:
        return []
    ids = tuple(run["id"] for run in runs)
    items: dict[int, list[dict]] = {run["id"]: [] for run in runs}
    for row in conn.execute(
        f"""
        SELECT id, advice_id, key, title, severity, detail, action, evidence, status, act_json
          FROM advice_items WHERE advice_id IN ({",".join("?" * len(ids))})
         ORDER BY id
        """,  # noqa: S608
        ids,
    ):
        items[row["advice_id"]].append(dict(row))
    for run in runs:
        run["items"] = items[run["id"]]
        _attach_mentioned_sessions(conn, run["items"], now)
        _attach_projects(conn, run["items"])
    _attach_acts(conn, [item for run in runs for item in run["items"]])
    return runs


def _attach_acts(conn: sqlite3.Connection, items: list[dict]) -> None:
    """The typed action of a tip and the patch already carried out by it (task D7).

    The import is local: `actions` reads the session state through this module, and at
    the top of the file the two would import each other.
    """
    from . import actions

    patches = actions.patches_for_items(conn, [int(item["id"]) for item in items])
    for item in items:
        raw = item.pop("act_json", None)
        try:
            item["act"] = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            item["act"] = None
        item["patch"] = patches.get(int(item["id"]))


#: How the advisor refers to a session: by the short id from the digest.
#: It never sees the full uuid - the digest carries the same short form.
_SESSION_MENTION = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\b[0-9a-f]{8}\b")


#: What `session_status` reads besides the columns the card shows. They are dropped once
#: the status is counted: the screen needs the answer, not the way to it.
_STATUS_COLUMNS = (
    "last_record_kind",
    "last_record_at",
    "last_stop_reason",
    "is_live",
    "busy_since",
    "otel_seen_at",
    "tool_decided_at",
)


def _attach_mentioned_sessions(
    conn: sqlite3.Connection, items: list[dict], now: datetime | None = None
) -> None:
    """Expand the ids mentioned in a tip into a session name, a project and a status.

    The session title never reaches the digest - it is a retelling of the conversation
    (SPEC §7). On screen it is needed though: "b2ae5a8a" tells a human nothing. So we
    expand it here, at display time, and the title never leaves the machine.

    A tip lives longer than the state it describes: by the time it is read the session
    may have moved on or closed. Hence the last activity and the status alongside the
    name, counted by the same rule as the two session lists - so that a card does not
    send one to close a session that finished by itself an hour ago.
    """
    prefixes = {
        mention[:8]
        for item in items
        for field in ("title", "detail", "action", "evidence")
        for mention in _SESSION_MENTION.findall(item.get(field) or "")
    }
    if not prefixes:
        for item in items:
            item["sessions"] = []
        return
    known = {
        row["short"]: dict(row)
        for row in conn.execute(
            f"""
            SELECT substr(s.id, 1, 8) AS short, s.id, s.title, s.last_at,
                   s.last_record_kind, s.last_record_at, s.last_stop_reason,
                   s.is_live, s.busy_since,
                   {OTEL_SESSION_COLUMNS}
                   COALESCE(p.display_name, p.slug) AS project
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE substr(s.id, 1, 8) IN ({",".join("?" * len(prefixes))})
            """,  # noqa: S608
            tuple(prefixes),
        )
    }
    moment = now or datetime.now(UTC)
    for row in known.values():
        row["status"] = session_status(row, moment)
        for column in _STATUS_COLUMNS:
            row.pop(column, None)
    # The prompt log of the mentioned sessions: on the card it is the shortest answer to
    # "what was this session about" - shorter than a title and honest (task C7).
    ends = prompt_ends(conn, [session["id"] for session in known.values()])
    for row in known.values():
        log = ends.get(row["id"]) or {}
        row["prompts"] = log.get("prompts") or []
        row["prompt_count"] = log.get("total") or 0
    for item in items:
        seen: dict[str, dict] = {}
        for field in ("title", "detail", "action", "evidence"):
            for mention in _SESSION_MENTION.findall(item.get(field) or ""):
                session = known.get(mention[:8])
                if session is not None:
                    seen[session["id"]] = session
        item["sessions"] = list(seen.values())


def session_prompts(conn: sqlite3.Connection, session_id: str, limit: int = 500) -> list[dict]:
    """The whole prompt log of a session in order (task C7).

    The text never leaves the machine: it is read by the screen, and the advisor digest
    is built from aggregates and knows nothing about this table (SPEC §7).
    """
    return [
        dict(row)
        for row in conn.execute(
            "SELECT ts, text FROM prompts WHERE session_id = ? ORDER BY ts, id LIMIT ?",
            (session_id, limit),
        )
    ]


def prompt_ends(conn: sqlite3.Connection, session_ids: list[str]) -> dict[str, dict]:
    """The first and the last prompt of every session, plus how many there are in all.

    The ends are what the card shows by default: the beginning says what the session was
    started for, the end says what it has come to. Everything in between opens on demand,
    so a screen with twenty tips does not drag the whole log along.
    """
    if not session_ids:
        return {}
    holes = ",".join("?" * len(session_ids))
    ends: dict[str, dict] = {}
    for row in conn.execute(
        f"""
        SELECT session_id, ts, text, total FROM (
            SELECT session_id, ts, text,
                   ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY ts, id) AS rn,
                   COUNT(*) OVER (PARTITION BY session_id) AS total
              FROM prompts WHERE session_id IN ({holes})
        ) WHERE rn = 1 OR rn = total
         ORDER BY session_id, ts, rn
        """,  # noqa: S608
        tuple(session_ids),
    ):
        found = ends.setdefault(row["session_id"], {"total": row["total"], "prompts": []})
        found["prompts"].append({"ts": row["ts"], "text": row["text"]})
    return ends


def _attach_projects(conn: sqlite3.Connection, items: list[dict]) -> None:
    """Which projects a tip is about - so the screen can cut the advice by project.

    Two sources, because one is not enough: the projects of the sessions a tip mentions,
    and the project names written in the text itself. Most tips name no session at all
    ("close the sprawling session in 'briefly'"), and by sessions alone two thirds of them
    would end up with no project.
    """
    # A name of one character (the root project is called "-") would be found in any text.
    known = [
        row["name"]
        for row in conn.execute(
            "SELECT DISTINCT COALESCE(display_name, slug) AS name FROM projects"
            " WHERE length(COALESCE(display_name, slug)) > 1"
        )
    ]
    # A dash is not a word boundary for `\b`, and "cloudo" would then be found inside
    # "cloudo-dash". The longer name wins because the shorter one is not matched at all.
    patterns = [
        (name, re.compile(rf"(?<![\w\-./]){re.escape(name)}(?![\w\-./])", re.IGNORECASE))
        for name in known
    ]
    for item in items:
        found = {session["project"] for session in item["sessions"] if session["project"]}
        fields = ("title", "detail", "action", "evidence")
        text = " ".join(item.get(field) or "" for field in fields)
        found.update(name for name, pattern in patterns if pattern.search(text))
        item["projects"] = sorted(found)


def set_advice_status(conn: sqlite3.Connection, item_id: int, status: str) -> bool:
    """Mark a tip as accepted, dismissed or put it back to new.

    A dismissed one travels into the next tick's prompt marked "do not repeat" -
    that is what makes the status valuable (SPEC §5).
    """
    if status not in ADVICE_STATUSES:
        raise ValueError(f"unknown tip status: {status}")
    with conn:
        cursor = conn.execute("UPDATE advice_items SET status = ? WHERE id = ?", (status, item_id))
    return cursor.rowcount > 0


#: How many points the session spend sparkline holds: a bar narrower than a couple of
#: pixels cannot be made out on screen anyway, and the more points there are the less
#: data each one needs.
SPARK_POINTS = 24


#: What telemetry knows about a session: when it last saw any event for it and when
#: the last permission decision came. Both session lists take these columns so that the
#: status follows a single rule (milestone E).
OTEL_SESSION_COLUMNS = """
                   (SELECT MAX(e.ts) FROM otel_events AS e
                     WHERE e.session_id = s.id)                        AS otel_seen_at,
                   (SELECT MAX(e.ts) FROM otel_events AS e
                     WHERE e.session_id = s.id
                       AND e.name = 'tool_decision')                   AS tool_decided_at,
"""


def sessions_page(
    conn: sqlite3.Connection,
    *,
    project: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> list[dict]:
    """The "Sessions" screen: a list with filters, a sparkline and the resume link (task C1).

    The status follows the same rule as on "Overview", but the filtering happens in
    Python: it is derived from several fields, and moving that into SQL would mean
    duplicating the rule.
    """
    moment = now or datetime.now(UTC)
    clause = ""
    params: list[Any] = []
    if project:
        clause += " AND p.slug LIKE ?"
        params.append(f"%{project}%")
    if since is not None:
        clause += " AND s.last_at >= ?"
        params.append(_utc_stamp(since))
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT s.id, COALESCE(p.display_name, p.slug) AS project, p.root_path,
                   s.title, s.first_prompt, s.last_prompt, s.started_at, s.last_at,
                   s.turns, s.tokens_out, s.cache_read, s.cache_write, s.cost_usd,
                   s.last_context, s.parent_session_id, s.is_live, s.busy_since,
                   s.last_record_kind, s.last_record_at, s.last_stop_reason,
                   {OTEL_SESSION_COLUMNS}
                   (SELECT COUNT(*) FROM sessions AS child
                     WHERE child.parent_session_id = s.id)             AS children,
                   (SELECT COALESCE(SUM(t.is_sidechain), 0) FROM turns AS t
                     WHERE t.session_id = s.id)                        AS sidechain_turns
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE s.hidden = 0{clause}
             ORDER BY s.last_at DESC LIMIT ?
            """,  # noqa: S608
            (*params, limit),
        )
    ]
    for row in rows:
        row["status"] = session_status(row, moment)
        row["tokens"] = row["tokens_out"] + row["cache_read"] + row["cache_write"]
    if status:
        rows = [row for row in rows if row["status"] == status]
    _attach_sparklines(conn, rows)
    return rows


def _attach_sparklines(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Draw each session's spend over time - in a single query for all of them."""
    for row in rows:
        row["spark"] = []
    by_id = {row["id"]: row for row in rows}
    if not by_id:
        return
    ids = tuple(by_id)
    marks = conn.execute(
        f"""
        SELECT session_id,
               MIN(CAST(strftime('%s', ts) AS INTEGER)) AS started,
               MAX(CAST(strftime('%s', ts) AS INTEGER)) AS ended
          FROM turns WHERE session_id IN ({",".join("?" * len(ids))})
         GROUP BY session_id
        """,  # noqa: S608
        ids,
    ).fetchall()
    spans = {row["session_id"]: (row["started"], row["ended"]) for row in marks}
    buckets: dict[str, list[int]] = {sid: [0] * SPARK_POINTS for sid in spans}
    for row in conn.execute(
        f"""
        SELECT session_id, CAST(strftime('%s', ts) AS INTEGER) AS at,
               input_tokens + output_tokens + cache_read
             + cache_write_5m + cache_write_1h AS tokens
          FROM turns WHERE session_id IN ({",".join("?" * len(ids))})
        """,  # noqa: S608
        ids,
    ):
        started, ended = spans[row["session_id"]]
        span = max(ended - started, 1)
        index = min(int((row["at"] - started) / span * SPARK_POINTS), SPARK_POINTS - 1)
        buckets[row["session_id"]][index] += row["tokens"]
    for session_id, spark in buckets.items():
        by_id[session_id]["spark"] = spark


# --- overview (task A5) -------------------------------------------------------

#: Burn rate windows in seconds. The short ones are "what is happening right now":
#: a turn drops hundreds of thousands of tokens into the window at once, and in a
#: one-minute average that shows up as a minute-long step (SPEC §4). The dashboard
#: gauge shows only the short ones; the minute stays for the notifier and the tray.
BURN_WINDOWS = (5, 10, 60, 300, 3600)

#: The live needle is not a window but an exponential decay: a turn kicks the value
#: up and silence lets it fall with a ~21-second half-life. A rectangular window
#: cannot behave like that - a burst either sits in it whole or drops out at once,
#: so the needle teleports instead of gliding.
LIVE_TAU_SECONDS = 30

#: Beyond six tau a turn weighs under 0.3% of itself - not worth reading.
LIVE_CUTOFF_SECONDS = LIVE_TAU_SECONDS * 6

#: The time format in transcripts: UTC with Z. String comparison is correct here and
#: lets us filter by time right in SQL, without parsing dates.
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TS_FORMAT)


def project_filter(project: str | None, column: str = "session_id") -> tuple[str, list[str]]:
    """The "session from this project" condition and its parameters (task B7).

    The search is a substring of the slug - the transcript directory name: it holds the
    whole path, so `myapp` finds `-Users-me-code-myapp`.
    """
    if not project:
        return "", []
    clause = (
        f" AND {column} IN (SELECT s.id FROM sessions AS s"
        " JOIN projects AS p ON p.id = s.project_id WHERE p.slug LIKE ?)"
    )
    return clause, [f"%{project}%"]


def advisor_cost(conn: sqlite3.Connection, since: datetime) -> dict:
    """What the advisor itself cost over the period (task C4, SPEC §10).

    An instrument that costs more than it saves is a bad instrument, so its own spend
    is shown next to everyone else's. A tick on haiku costs about $0.07: almost all of
    it is writing the Claude Code system prompt into the cache, and there is nothing
    left to trim (see CLAUDE.md on `claude -p`).
    """
    row = conn.execute(
        "SELECT COUNT(*) AS ticks, COALESCE(SUM(cost_usd), 0) AS cost_usd,"
        "       MAX(ts) AS last_at"
        "  FROM advice WHERE ts >= ?",
        (_utc_stamp(since),),
    ).fetchone()
    by_kind = [
        dict(item)
        for item in conn.execute(
            "SELECT kind, COUNT(*) AS ticks, COALESCE(SUM(cost_usd), 0) AS cost_usd"
            "  FROM advice WHERE ts >= ? GROUP BY kind ORDER BY cost_usd DESC",
            (_utc_stamp(since),),
        )
    ]
    return dict(row) | {"by_kind": by_kind}


def window_usage(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    project: str | None = None,
) -> dict:
    """Totals over the turns inside a time interval."""
    params: list[str] = [_utc_stamp(since)]
    clause = "ts >= ?"
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
    project_clause, project_params = project_filter(project)
    clause += project_clause
    params += project_params
    row = conn.execute(
        f"""
        SELECT COUNT(*)                            AS turns,
               COUNT(DISTINCT session_id)          AS sessions,
               COALESCE(SUM(input_tokens), 0)      AS input_tokens,
               COALESCE(SUM(output_tokens), 0)     AS output_tokens,
               COALESCE(SUM(cache_read), 0)        AS cache_read,
               COALESCE(SUM(cache_write_5m), 0)    AS cache_write_5m,
               COALESCE(SUM(cache_write_1h), 0)    AS cache_write_1h,
               COALESCE(SUM(cost_usd), 0)          AS cost_usd
          FROM turns WHERE {clause}
        """,  # noqa: S608 - parameters are bound through placeholders
        params,
    ).fetchone()
    usage = dict(row)
    usage["cache_write"] = usage["cache_write_5m"] + usage["cache_write_1h"]
    # The full volume of tokens that went through the model: that is what drives the needle.
    usage["tokens"] = (
        usage["input_tokens"] + usage["output_tokens"] + usage["cache_read"] + usage["cache_write"]
    )
    return usage


def window_key(seconds: int) -> str:
    """The window key in the API answer: 5s, 10s, 1m, 5m, 60m."""
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m"


def live_rate(conn: sqlite3.Connection, now: datetime) -> dict:
    """The `live` burn entry: exponentially weighted rates (see LIVE_TAU_SECONDS).

    Unlike the windows, `usage` here holds per-minute rates rather than totals: each
    component is weighted the same way as the needle, so the breakdown shares stay
    honest and the legend sums to the needle value.
    """
    rows = conn.execute(
        "SELECT (julianday(?) - julianday(ts)) * 86400.0 AS age,"
        "       session_id, input_tokens, output_tokens, cache_read,"
        "       cache_write_5m, cache_write_1h, cost_usd"
        "  FROM turns WHERE ts >= ?",
        (_utc_stamp(now), _utc_stamp(now - timedelta(seconds=LIVE_CUTOFF_SECONDS))),
    ).fetchall()
    parts = dict.fromkeys(
        (
            "input_tokens",
            "output_tokens",
            "cache_read",
            "cache_write_5m",
            "cache_write_1h",
            "cost_usd",
        ),
        0.0,
    )
    sessions = set()
    tau_minutes = LIVE_TAU_SECONDS / 60
    for row in rows:
        # the kernel integrates to one: a steady X tokens/min stream reads as X
        weight = math.exp(-max(row["age"], 0.0) / LIVE_TAU_SECONDS) / tau_minutes
        for key in parts:
            parts[key] += (row[key] or 0) * weight
        sessions.add(row["session_id"])
    usage: dict[str, Any] = dict(parts)
    usage["turns"] = len(rows)
    usage["sessions"] = len(sessions)
    usage["cache_write"] = usage["cache_write_5m"] + usage["cache_write_1h"]
    usage["tokens"] = (
        usage["input_tokens"] + usage["output_tokens"] + usage["cache_read"] + usage["cache_write"]
    )
    return {
        "tokens_per_min": usage["tokens"],
        "output_per_min": usage["output_tokens"],
        "cost_per_hour": usage["cost_usd"] * 60,
        "turns": len(rows),
        "sessions": len(sessions),
        "window_seconds": LIVE_TAU_SECONDS,
        "usage": usage,
    }


def burn_rates(conn: sqlite3.Connection, now: datetime) -> dict[str, dict]:
    """Burn rate over the SPEC §4 windows - always tokens per minute, the windows differ."""
    rates: dict[str, dict] = {"live": live_rate(conn, now)}
    for seconds in BURN_WINDOWS:
        usage = window_usage(conn, now - timedelta(seconds=seconds))
        minutes = seconds / 60
        rates[window_key(seconds)] = {
            "tokens_per_min": usage["tokens"] / minutes,
            "output_per_min": usage["output_tokens"] / minutes,
            "cost_per_hour": usage["cost_usd"] / minutes * 60,  # prices are task B1
            "turns": usage["turns"],
            "sessions": usage["sessions"],
            "window_seconds": seconds,
            # Absolute totals for the window: the per-part breakdown is computed
            # from them, and it must be able to show any window.
            "usage": usage,
        }
    return rates


#: How many live sessions to show on the dashboard.
LIVE_LIMIT = 5

#: The window in which a session still counts as recent and makes it to the dashboard.
LIVE_WINDOW_SECONDS = 3600

#: After this pause a session stops being "now" and goes idle.
IDLE_AFTER_SECONDS = 120

#: How long we wait for a tool answer before looking at processes: until then any
#: tool counts as running, nobody asks for a permission that fast.
PERMISSION_AFTER_SECONDS = 25

#: How much older than the tool request record a child may turn out to be:
#: the transcript is appended in bursts every 2-6 s and lags behind the process start.
CHILD_LAG_SECONDS = 10

#: The same wait when telemetry works: the permission decision arrives as an event,
#: and waiting a quarter of a minute is pointless - events show up within seconds.
#: The threshold is still not zero: logs are exported in batches every few seconds.
OTEL_PERMISSION_AFTER_SECONDS = 10

#: How far events may lag behind a turn before telemetry counts as having gone
#: quiet (it could have been switched off by restarting Claude Code without the variables).
OTEL_STALE_SECONDS = 60

#: Session statuses - by whom the session is waiting for at that moment.
STATUS_WORKING = "working"  # the turn is unfinished: the model thinks or drives tools
STATUS_PERMISSION = "permission"  # a tool was requested, no answer - a permission hangs
STATUS_ANSWERED = "answered"  # the model answered and waits for the human
STATUS_IDLE = "idle"  # silence longer than IDLE_AFTER_SECONDS, but the process lives
STATUS_DONE = "done"  # no process: nothing will be written into this session again


def session_status(row: dict, now: datetime) -> str:
    """Whom the session is waiting for right now.

    An unfinished turn means the model is working: both while it thinks over the prompt
    and while it drives tools. A finished turn means the opposite: the human is awaited.
    A special case is a requested tool with no result: that is either a long tool or a
    hanging permission request, and in the transcript the two look exactly the same.
    `_tool_is_running` tells them apart - by processes.

    Silence on its own does not separate a pause from the end of work: the transcript
    does not know the session was closed. `is_live` knows that - the flag is set from the
    list of Claude Code processes (task B4). A missing process counts only after
    `IDLE_AFTER_SECONDS`: the flag is not refreshed instantly, and a live session
    must not blink "finished" between polls.

    Where telemetry is on, guessing by processes is not needed at all: the permission
    decision arrives as a `tool_decision` event (milestone E). Then "the tool is running"
    is a fact rather than an inference from the process tree, and tools without a process
    of their own (MCP calls, `WebFetch`) stop looking like waiting.
    """
    quiet = _seconds_since(row.get("last_record_at") or row.get("last_at"), now)
    kind = row.get("last_record_kind")

    if kind == "assistant" and row.get("last_stop_reason") == "tool_use":
        if _tool_is_allowed(row):
            return STATUS_WORKING
        if quiet < _permission_delay(row):
            return STATUS_WORKING
        if _otel_active(row):  # telemetry is quiet about a decision - so there is none
            return STATUS_PERMISSION
        if _tool_is_running(row):
            return STATUS_WORKING
        return STATUS_PERMISSION
    if quiet >= IDLE_AFTER_SECONDS:
        return STATUS_DONE if row.get("is_live") == 0 else STATUS_IDLE
    if kind in {"prompt", "tool_result"}:
        return STATUS_WORKING
    return STATUS_ANSWERED


def _permission_delay(row: dict) -> float:
    """How long to wait for a tool answer before calling it a permission."""
    return OTEL_PERMISSION_AFTER_SECONDS if _otel_active(row) else PERMISSION_AFTER_SECONDS


def _otel_active(row: dict) -> bool:
    """Whether this session is sending telemetry right now.

    Old events alone are not enough: telemetry could have been switched off mid-work, and
    then silence would mean not "there is no decision" but "there is no data". Events come
    on every turn, so freshness is checked against the last turn.
    """
    seen = _moment(row.get("otel_seen_at"))
    if seen is None:
        return False
    asked = _moment(row.get("last_record_at") or row.get("last_at"))
    return asked is None or seen >= asked - timedelta(seconds=OTEL_STALE_SECONDS)


def _tool_is_allowed(row: dict) -> bool:
    """Whether the tool the model asks for has already been allowed (milestone E).

    The `tool_decision` event arrives both for an automatic allow and for a human answer,
    so a decision later than the request means one thing: the session is not waiting, it
    is working. The lag is the same as with processes: the transcript is written in
    bursts, and events leave in batches every few seconds.
    """
    decided, asked = _moment(row.get("tool_decided_at")), _moment(row.get("last_record_at"))
    if decided is None or asked is None:
        return False
    return decided >= asked - timedelta(seconds=CHILD_LAG_SECONDS)


def _tool_is_running(row: dict) -> bool:
    """Whether the session drives a tool right now - by processes (task B4).

    The sign of work is a child of the session process started no earlier than the tool
    request. Permanent children (MCP servers) and background commands started earlier and
    do not count, hence the comparison by time rather than "there are children".
    While processes have not been polled (`is_live IS NULL`) there is no sign - then the
    old guess about a hanging permission is what is left.
    """
    if row.get("is_live") is None:
        return False
    started, asked = _moment(row.get("busy_since")), _moment(row.get("last_record_at"))
    if started is None or asked is None:
        return False
    return started >= asked - timedelta(seconds=CHILD_LAG_SECONDS)


def _moment(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _seconds_since(stamp: str | None, now: datetime) -> float:
    moment = _moment(stamp)
    if moment is None:
        return float("inf")
    return (now - moment).total_seconds()


def live_sessions(
    conn: sqlite3.Connection,
    now: datetime,
    seconds: int = LIVE_WINDOW_SECONDS,
    limit: int = 40,
) -> list[dict]:
    """Recent sessions with their statuses (liveness refinement is task B4).

    Manually hidden ones are not shown, the order is by last activity.
    """
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT s.id, COALESCE(p.display_name, p.slug) AS project, p.root_path,
                   s.last_at, s.started_at,
                   s.turns, s.tokens_out, s.last_context, s.first_prompt, s.last_prompt,
                   s.title, s.title_source, s.last_record_kind, s.last_record_at,
                   s.last_stop_reason, s.is_live, s.busy_since,
                   {OTEL_SESSION_COLUMNS}
                   (SELECT COALESCE(SUM(t.output_tokens), 0) FROM turns AS t
                     WHERE t.session_id = s.id AND t.ts >= ?) AS output_recent
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE s.last_at >= ? AND s.hidden = 0
             ORDER BY s.last_at DESC
             LIMIT ?
            """,  # noqa: S608
            (_utc_stamp(now - timedelta(seconds=IDLE_AFTER_SECONDS)),)
            + (_utc_stamp(now - timedelta(seconds=seconds)), limit),
        )
    ]
    for row in rows:
        row["status"] = session_status(row, now)
    return rows


def session_chain(conn: sqlite3.Connection, session_id: str) -> dict:
    """The whole work line the session belongs to (task B5).

    Resume copies the history into a new `sessionId`, so one piece of work is scattered
    over several sessions. The line is built from the root of the chain downwards;
    turns are not doubled in the process - the copies were swallowed by deduplication
    on `message_id` back at import time.
    """
    rows = conn.execute(
        """
        WITH RECURSIVE up(id, parent) AS (
            SELECT id, parent_session_id FROM sessions WHERE id = :id
            UNION
            SELECT s.id, s.parent_session_id FROM sessions AS s JOIN up ON up.parent = s.id
        ),
        root AS (SELECT id FROM up WHERE parent IS NULL LIMIT 1),
        down(id) AS (
            SELECT id FROM root
            UNION
            SELECT s.id FROM sessions AS s JOIN down ON s.parent_session_id = down.id
        )
        SELECT d.id,
               (SELECT COUNT(*) FROM turns AS t WHERE t.session_id = d.id)          AS turns,
               (SELECT COALESCE(SUM(t.input_tokens + t.output_tokens + t.cache_read
                                  + t.cache_write_5m + t.cache_write_1h), 0)
                  FROM turns AS t WHERE t.session_id = d.id)                        AS tokens,
               (SELECT COALESCE(SUM(t.cost_usd), 0) FROM turns AS t
                 WHERE t.session_id = d.id)                                         AS cost_usd
          FROM down AS d
        """,
        {"id": session_id},
    ).fetchall()
    sessions = [dict(row) for row in rows]
    return {
        "sessions": [row["id"] for row in sessions],
        "turns": sum(row["turns"] for row in sessions),
        "tokens": sum(row["tokens"] for row in sessions),
        "cost_usd": sum(row["cost_usd"] for row in sessions),
    }


def refresh_liveness(conn: sqlite3.Connection, active: Mapping[str, datetime | None] | None) -> int:
    """Set `is_live` and `busy_since` from the live Claude Code sessions (task B4).

    `active=None` means "asking failed" - then the flags stay as they were: a stale
    liveness beats a false "finished" on everything. A session's value is the start
    moment of its youngest child (see `processes`).
    Returns the number of changed rows.
    """
    if active is None:
        return 0
    ids = tuple(active)
    holes = ",".join("?" * len(ids))
    changed = 0
    with conn:
        # The dead ones: liveness goes out together with busyness - there is no process.
        changed += conn.execute(
            f"UPDATE sessions SET is_live = 0, busy_since = NULL"  # noqa: S608
            f" WHERE (is_live IS NOT 0 OR busy_since IS NOT NULL)"
            f"{f' AND id NOT IN ({holes})' if ids else ''}",
            ids,
        ).rowcount
        for session_id, started in active.items():
            stamp = _utc_stamp(started) if started else None
            changed += conn.execute(
                "UPDATE sessions SET is_live = 1, busy_since = :stamp"
                " WHERE id = :id AND (is_live IS NOT 1 OR busy_since IS NOT :stamp)",
                {"id": session_id, "stamp": stamp},
            ).rowcount
    return changed


def top_sessions(conn: sqlite3.Connection, since: datetime, limit: int = 5) -> list[dict]:
    """The most expensive sessions of the period."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, COALESCE(p.display_name, p.slug) AS project, s.last_at,
                   s.first_prompt, s.title, s.last_context,
                   COUNT(t.id)                        AS turns,
                   COALESCE(SUM(t.output_tokens), 0)  AS output_tokens,
                   COALESCE(SUM(t.input_tokens + t.output_tokens
                                + t.cache_read + t.cache_write_5m
                                + t.cache_write_1h), 0) AS tokens
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
              JOIN turns AS t ON t.session_id = s.id AND t.ts >= ?
             GROUP BY s.id
             ORDER BY tokens DESC LIMIT ?
            """,
            (_utc_stamp(since), limit),
        )
    ]


def local_day_start(now: datetime) -> datetime:
    """The start of today in local time: "today" is about the human."""
    local = now.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


#: How telemetry marks Claude Code service requests. The main work goes as `main`,
#: subagents as `subagent`, and both are visible in the transcript.
#: `auxiliary` is not visible there at all: session title generation, for instance.
OTEL_OFF_TRANSCRIPT = "auxiliary"

#: Permission decision sources that mean a human answer - the rest
#: (`config`, `hook`) worked on their own and need no attention.
OTEL_MANUAL_SOURCES = ("user_permanent", "user_temporary", "user_abort", "user_reject")


def otel_usage(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    project: str | None = None,
) -> dict:
    """Spend that is absent from transcripts (milestone E).

    Claude Code service requests are separate model calls, and nothing of them remains
    in the JSONL: the dashboard understates the spend by exactly this much.
    It is counted from telemetry alone; without it every number is zero.
    """
    clause = "ts >= ?"
    params: list[Any] = [_utc_stamp(since)]
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
    project_clause, project_params = project_filter(project)
    clause += project_clause
    params += project_params
    source = " AND json_extract(attrs, '$.query_source') = ?"

    tokens = {
        row["kind"]: row["value"]
        for row in conn.execute(
            f"SELECT kind, COALESCE(SUM(value), 0) AS value FROM otel_metrics"
            f" WHERE name = 'claude_code.token.usage' AND {clause}{source}"
            f" GROUP BY kind",  # noqa: S608
            (*params, OTEL_OFF_TRANSCRIPT),
        )
    }
    cost = conn.execute(
        f"SELECT COALESCE(SUM(value), 0) FROM otel_metrics"
        f" WHERE name = 'claude_code.cost.usage' AND {clause}{source}",  # noqa: S608
        (*params, OTEL_OFF_TRANSCRIPT),
    ).fetchone()[0]
    main_cost = conn.execute(
        f"SELECT COALESCE(SUM(value), 0) FROM otel_metrics"
        f" WHERE name = 'claude_code.cost.usage' AND {clause}"  # noqa: S608
        f" AND json_extract(attrs, '$.query_source') <> ?",
        (*params, OTEL_OFF_TRANSCRIPT),
    ).fetchone()[0]
    # The kinds of service requests are known only to events: in the metrics they are
    # all lumped into `auxiliary`, while `api_request` names each one.
    kinds = [
        dict(row)
        for row in conn.execute(
            f"SELECT json_extract(attrs, '$.query_source') AS source, COUNT(*) AS requests,"
            f"       COALESCE(SUM(json_extract(attrs, '$.cost_usd')), 0) AS cost_usd"
            f"  FROM otel_events WHERE name = 'api_request' AND {clause}"  # noqa: S608
            f" GROUP BY source ORDER BY cost_usd DESC",
            params,
        )
    ]
    return {
        "tokens": sum(tokens.values()),
        "input_tokens": tokens.get("input", 0),
        "output_tokens": tokens.get("output", 0),
        "cache_read": tokens.get("cacheRead", 0),
        "cache_write": tokens.get("cacheCreation", 0),
        "cost_usd": cost,
        # The share of service work in what telemetry saw over the same period.
        "share": cost / (cost + main_cost) if cost + main_cost else 0.0,
        "request_kinds": kinds,
    }


#: How many tools to show in the confirmation breakdown. Beyond that the tail decides
#: nothing, while in the advisor digest it eats budget: a machine can carry dozens of
#: MCP tools.
PERMISSION_TOOLS = 12


def otel_permissions(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    project: str | None = None,
    limit: int = PERMISSION_TOOLS,
) -> dict:
    """Decisions on permission requests (milestone E).

    Claude Code writes neither the "allow?" question nor the answer to it into the
    transcript, so before telemetry this number did not exist at all. Manual
    confirmations are a direct reason to fix `permissions` in the settings: each of them
    stops the work and waits for the human.
    """
    clause = "ts >= ?"
    params: list[Any] = [_utc_stamp(since)]
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
    project_clause, project_params = project_filter(project)
    clause += project_clause
    params += project_params
    rows = [
        dict(row)
        for row in conn.execute(
            f"SELECT json_extract(attrs, '$.tool_name') AS tool,"
            f"       json_extract(attrs, '$.source')    AS source,"
            f"       json_extract(attrs, '$.decision')  AS decision,"
            f"       COUNT(*) AS decisions"
            f"  FROM otel_events WHERE name = 'tool_decision' AND {clause}"  # noqa: S608
            f" GROUP BY tool, source, decision",
            params,
        )
    ]
    manual: dict[str, int] = {}
    total = 0
    manual_total = 0
    rejected = 0
    for row in rows:
        total += row["decisions"]
        if row["decision"] == "reject":
            rejected += row["decisions"]
        if row["source"] in OTEL_MANUAL_SOURCES:
            manual_total += row["decisions"]
            tool = row["tool"] or "-"
            manual[tool] = manual.get(tool, 0) + row["decisions"]
    # Mode switches are the same subject from the other side: if a human goes into
    # acceptEdits over and over, the permission rules are in their way.
    modes = [
        dict(row)
        for row in conn.execute(
            f"SELECT json_extract(attrs, '$.to_mode') AS mode, COUNT(*) AS switches"
            f"  FROM otel_events WHERE name = 'permission_mode_changed' AND {clause}"  # noqa: S608
            f" GROUP BY mode ORDER BY switches DESC",
            params,
        )
    ]
    return {
        "decisions": total,
        "manual": manual_total,
        "auto": total - manual_total,
        "rejected": rejected,
        "by_tool": [
            {"tool": tool, "decisions": count}
            for tool, count in sorted(manual.items(), key=lambda item: -item[1])[:limit]
        ],
        "mode_switches": modes,
    }


def otel_errors(conn: sqlite3.Connection, since: datetime) -> dict:
    """API errors and refusals over the period (milestone E).

    A failed request does not reach the transcript at all: there you only see what the
    model answered in the end. Retries after a 429 or a 529 cost time in the meantime,
    and knowing about them is useful.
    """
    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT COALESCE(json_extract(attrs, '$.status_code'), '-') AS status,"
            "       COUNT(*) AS errors"
            "  FROM otel_events WHERE name IN ('api_error', 'api_refusal') AND ts >= ?"
            " GROUP BY status ORDER BY errors DESC",
            (_utc_stamp(since),),
        )
    ]
    # A failure inside Claude Code itself is a different trouble than a network refusal:
    # the work breaks off midway, and the tokens spent on it are not coming back.
    internal = [
        dict(row)
        for row in conn.execute(
            "SELECT COALESCE(json_extract(attrs, '$.error_name'), '-') AS error,"
            "       COUNT(*) AS count"
            "  FROM otel_events WHERE name = 'internal_error' AND ts >= ?"
            " GROUP BY error ORDER BY count DESC",
            (_utc_stamp(since),),
        )
    ]
    return {
        "errors": sum(row["errors"] for row in rows),
        "by_status": rows,
        "internal": internal,
    }


def otel_mcp(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    project: str | None = None,
) -> dict:
    """What MCP servers cost at session start (milestone E).

    The `mcp_server_connection` event knows the connection time and its outcome, while
    the transcript knows only tool calls. A server that takes seconds to connect on every
    start and was never useful is visible only this way.
    The name comes from `server_name` (with `OTEL_LOG_TOOL_DETAILS=1`) or from
    `plugin.name` for servers that come from plugins.
    """
    clause = "ts >= ?"
    params: list[Any] = [_utc_stamp(since)]
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
    project_clause, project_params = project_filter(project)
    clause += project_clause
    params += project_params
    rows = [
        dict(row)
        for row in conn.execute(
            # A key with a dot inside the name: without quotes the path would read as
            # a nested object `plugin` with a field `name`.
            f"SELECT COALESCE(json_extract(attrs, '$.server_name'),"
            f"                json_extract(attrs, '$.\"plugin.name\"'), '-') AS server,"
            f"       json_extract(attrs, '$.status')                      AS status,"
            f"       COUNT(*)                                             AS events,"
            f"       COUNT(DISTINCT session_id)                           AS sessions,"
            f"       SUM(CAST(json_extract(attrs, '$.duration_ms') AS REAL)) / 1000.0 AS seconds"
            f"  FROM otel_events WHERE name = 'mcp_server_connection' AND {clause}"  # noqa: S608
            f" GROUP BY server, status",
            params,
        )
    ]
    servers: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = servers.setdefault(
            row["server"], {"server": row["server"], "connects": 0, "failures": 0, "seconds": 0.0}
        )
        if row["status"] == "connected":
            entry["connects"] += row["events"]
            # The connection time; for `disconnected` the same value means the
            # lifetime of the connection, and adding them together is wrong.
            entry["seconds"] += row["seconds"] or 0.0
        elif row["status"] == "failed":
            entry["failures"] += row["events"]
    sessions = conn.execute(
        f"SELECT COUNT(DISTINCT session_id) FROM otel_events"
        f" WHERE name = 'mcp_server_connection' AND {clause}",  # noqa: S608
        params,
    ).fetchone()[0]
    total = sum(entry["seconds"] for entry in servers.values())
    return {
        "servers": sorted(servers.values(), key=lambda item: -item["seconds"]),
        "connect_seconds": total,
        # What connecting costs one start: servers restart anew in every session,
        # and a total figure without that says little.
        "seconds_per_session": total / sessions if sessions else 0.0,
        "failures": sum(entry["failures"] for entry in servers.values()),
    }


def otel_prompts(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    project: str | None = None,
    limit: int = 12,
) -> dict:
    """Prompts and slash commands over the period (milestone E).

    In the transcript a slash command is `<command-name>` blocks instead of live text,
    and the parser does not unpack them; telemetry names the command directly.
    Only the length is taken from the prompt itself: we do not have the text and never will.
    """
    clause = "ts >= ?"
    params: list[Any] = [_utc_stamp(since)]
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
    project_clause, project_params = project_filter(project)
    clause += project_clause
    params += project_params
    row = conn.execute(
        f"SELECT COUNT(*) AS prompts,"
        f"       COALESCE(AVG(CAST(json_extract(attrs, '$.prompt_length') AS REAL)), 0) AS length"
        f"  FROM otel_events WHERE name = 'user_prompt' AND {clause}",  # noqa: S608
        params,
    ).fetchone()
    commands = [
        dict(command)
        for command in conn.execute(
            f"SELECT json_extract(attrs, '$.command_name')   AS command,"
            f"       json_extract(attrs, '$.command_source') AS source,"
            f"       COUNT(*)                                AS calls"
            f"  FROM otel_events WHERE name = 'user_prompt' AND {clause}"  # noqa: S608
            f"   AND json_extract(attrs, '$.command_name') IS NOT NULL"
            f" GROUP BY command, source ORDER BY calls DESC LIMIT ?",
            (*params, limit),
        )
    ]
    return {
        "prompts": row["prompts"],
        "avg_length": round(row["length"], 1),
        "commands": commands,
    }


#: How many seconds hooks must eat in total before it is worth talking about.
#: Fast hooks fit into single milliseconds, and their noise is not wanted.
HOOKS_WORTH_MENTIONING = 5.0


def otel_hooks(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    project: str | None = None,
) -> dict:
    """How much time Claude Code hooks eat (milestone E).

    A hook runs between turns and never reaches the transcript: there is just a pause
    between a prompt and an answer. Meanwhile an HTTP hook waiting on an unreachable
    service takes tens of seconds on every prompt - and noticing that is possible
    only here.
    """
    clause = "ts >= ?"
    params: list[Any] = [_utc_stamp(since)]
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
    project_clause, project_params = project_filter(project)
    clause += project_clause
    params += project_params
    rows = [
        dict(row)
        for row in conn.execute(
            f"SELECT COALESCE(json_extract(attrs, '$.hook_event'), '-') AS event,"
            f"       COUNT(*) AS runs,"
            f"       SUM(CAST(json_extract(attrs, '$.total_duration_ms') AS REAL)) / 1000.0"
            f"         AS seconds,"
            f"       MAX(CAST(json_extract(attrs, '$.total_duration_ms') AS REAL)) / 1000.0"
            f"         AS slowest,"
            f"       SUM(COALESCE(CAST(json_extract(attrs, '$.num_cancelled') AS INTEGER), 0)"
            f"           + COALESCE(CAST(json_extract(attrs,"
            f"                           '$.num_non_blocking_error') AS INTEGER), 0)) AS failures"
            f"  FROM otel_events WHERE name = 'hook_execution_complete' AND {clause}"  # noqa: S608
            f" GROUP BY event ORDER BY seconds DESC",
            params,
        )
    ]
    # What is declared at all: hooks are registered anew in every session, so unique
    # "event + type" pairs are counted rather than the sum of registrations.
    registered = [
        dict(row)
        for row in conn.execute(
            f"SELECT DISTINCT COALESCE(json_extract(attrs, '$.hook_event'), '-') AS event,"
            f"       COALESCE(json_extract(attrs, '$.hook_type'), '-')          AS type"
            f"  FROM otel_events WHERE name = 'hook_registered' AND {clause}"  # noqa: S608
            f" ORDER BY event",
            params,
        )
    ]
    return {
        "events": rows,
        "seconds": sum(row["seconds"] or 0.0 for row in rows),
        "failures": sum(row["failures"] or 0 for row in rows),
        "registered": registered,
    }


def otel_plugins(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    project: str | None = None,
) -> list[dict]:
    """Plugins that load in every session (milestone E).

    A plugin is free on its own, but what it brings along is not: an MCP server takes
    seconds to connect on every start, skills and commands take up room in the context.
    The transcript carries no trace of the loading at all.
    """
    clause = "ts >= ?"
    params: list[Any] = [_utc_stamp(since)]
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
    project_clause, project_params = project_filter(project)
    clause += project_clause
    params += project_params
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT COALESCE(json_extract(attrs, '$.\"plugin.name\"'), '-') AS plugin,"
            # The sign may be missing entirely: without COALESCE MAX(NULL) gives NULL,
            # and "no hooks" would become indistinguishable from "unknown".
            f"       COALESCE(MAX(json_extract(attrs, '$.has_mcp') IN (1, 'true')), 0)"
            f"         AS mcp,"
            f"       COALESCE(MAX(json_extract(attrs, '$.has_hooks') IN (1, 'true')), 0)"
            f"         AS hooks,"
            f"       MAX(COALESCE(CAST(json_extract(attrs,"
            f"                         '$.skill_path_count') AS INTEGER), 0)) AS skills,"
            f"       MAX(COALESCE(CAST(json_extract(attrs,"
            f"                         '$.command_path_count') AS INTEGER), 0)) AS commands"
            f"  FROM otel_events WHERE name = 'plugin_loaded' AND {clause}"  # noqa: S608
            f" GROUP BY plugin ORDER BY plugin",
            params,
        )
    ]


def otel_sessions(conn: sqlite3.Connection, since: datetime) -> dict:
    """Sessions through the eyes of telemetry and through the eyes of the parser (milestone E).

    The `session.count` metric marks starts by `start_type`: fresh, continued
    (`resume`, `continue`) or opened from the agents list.
    The transcript carries no such marking - there resume is visible only through copied
    turns. Next to it stands the number of sessions that reached the parser: a mismatch
    means one of the channels misses something, and that is worth a closer look.
    """
    starts = [
        dict(row)
        for row in conn.execute(
            "SELECT COALESCE(json_extract(attrs, '$.start_type'), '-') AS start_type,"
            "       COALESCE(SUM(value), 0) AS sessions"
            "  FROM otel_metrics WHERE name = 'claude_code.session.count' AND ts >= ?"
            " GROUP BY start_type ORDER BY sessions DESC",
            (_utc_stamp(since),),
        )
    ]
    seen = conn.execute(
        "SELECT COUNT(*) FROM (SELECT session_id FROM otel_events WHERE ts >= ?"
        " UNION SELECT session_id FROM otel_metrics WHERE ts >= ?)",
        (_utc_stamp(since), _utc_stamp(since)),
    ).fetchone()[0]
    indexed = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM turns WHERE ts >= ?", (_utc_stamp(since),)
    ).fetchone()[0]
    return {
        "starts": starts,
        "telemetry": seen,
        "transcripts": indexed,
        "resumed": sum(
            row["sessions"] for row in starts if row["start_type"] in ("resume", "continue")
        ),
    }


def otel_work(
    conn: sqlite3.Connection,
    since: datetime,
    until: datetime | None = None,
    project: str | None = None,
) -> dict:
    """What came out of the spend: lines of code and active time (milestone E).

    Claude Code counts both itself, and transcripts hold neither: the lines are the
    result of edits, and the active time excludes pauses, so it is shorter than the
    span between the first and the last turn.
    """
    clause = "ts >= ?"
    params: list[Any] = [_utc_stamp(since)]
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
    project_clause, project_params = project_filter(project)
    clause += project_clause
    params += project_params
    rows = {
        (row["name"], row["kind"]): row["value"]
        for row in conn.execute(
            f"SELECT name, kind, COALESCE(SUM(value), 0) AS value FROM otel_metrics"
            f" WHERE {clause} AND name IN ('claude_code.lines_of_code.count',"  # noqa: S608
            f"                             'claude_code.active_time.total',"
            f"                             'claude_code.commit.count')"
            f" GROUP BY name, kind",
            params,
        )
    }
    lines = "claude_code.lines_of_code.count"
    active = "claude_code.active_time.total"
    return {
        "lines_added": rows.get((lines, "added"), 0),
        "lines_removed": rows.get((lines, "removed"), 0),
        # The `type` of active time: user is the human at the keyboard,
        # cli is tool work and answer generation.
        "active_seconds": sum(value for (name, _), value in rows.items() if name == active),
        "waiting_seconds": rows.get((active, "cli"), 0),
        "commits": rows.get(("claude_code.commit.count", None), 0),
    }


def otel_state(conn: sqlite3.Connection, since: datetime) -> dict:
    """A telemetry slice for the overview: whether it works and what it sees (milestone E)."""
    last_at = conn.execute(
        "SELECT MAX(last) FROM (SELECT MAX(ts) AS last FROM otel_metrics"
        " UNION ALL SELECT MAX(ts) FROM otel_events)"
    ).fetchone()[0]
    return {
        "active": last_at is not None,
        "last_at": last_at,
        "off_transcript": otel_usage(conn, since),
        "permissions": otel_permissions(conn, since),
        "api": otel_errors(conn, since),
        "work": otel_work(conn, since),
        "hooks": otel_hooks(conn, since),
    }


def overview(
    conn: sqlite3.Connection, now: datetime | None = None, *, otel: dict | None = None
) -> dict:
    """The summary for the main screen (SPEC §5, "Overview").

    A ready telemetry slice can be passed in from outside: it is computed over tens of
    thousands of events and costs about 20 ms against 2 ms for all the rest of the
    overview, while refreshing no more often than the exporter sends (`tools/otel_bench.py`).
    """
    moment = now or datetime.now(UTC)
    day_start = local_day_start(moment)
    totals = conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM sessions) AS sessions,
               (SELECT COUNT(*) FROM turns)    AS turns,
               (SELECT COUNT(*) FROM projects) AS projects,
               (SELECT MAX(ts)  FROM turns)    AS last_turn_at
        """
    ).fetchone()
    return {
        "now": moment.astimezone(UTC).isoformat(),
        "burn": burn_rates(conn, moment),
        "today": window_usage(conn, day_start),
        "live_sessions": live_sessions(conn, moment),
        "live_limit": LIVE_LIMIT,
        "top_sessions": top_sessions(conn, day_start),
        "recent_turns": recent_turns(conn),
        "models": model_share(conn, day_start),
        "tools": tool_profile(conn, day_start),
        "idle": idle_turns(conn, day_start),
        "limits": limit_window(conn, moment),
        "series": burn_series(conn, moment),
        "stamps": data_stamps(conn, moment),
        "pending_sessions": pending_sessions(conn, moment),
        "otel": otel if otel is not None else otel_state(conn, day_start),
        # Our own spend next to everyone else's: the advisor must not eat more than
        # it saves (task C4).
        "advisor": advisor_cost(conn, day_start),
        "series_bucket_seconds": SERIES_BUCKET_SECONDS,
        "totals": dict(totals),
        # An empty dashboard explains itself: without this all three reasons look alike.
        "first_run": first_run(conn),
    }


def first_run(conn: sqlite3.Connection) -> dict[str, str | None]:
    """Why the dashboard is empty, when it is (SPEC §5).

    Zeros everywhere have three different causes and three different cures, and a person
    who has just installed cburn cannot tell them apart: the widgets all say the same
    polite "nothing yet". The kind is decided here and the words are the frontend's, as
    always. `ok` means there is data and there is nothing to explain.
    """
    turns = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    if turns:
        return {"kind": "ok", "transcripts": None}

    transcripts = paths.CLAUDE_PROJECTS_DIR
    if not transcripts.is_dir():
        # Claude Code is not installed, or it keeps its directory elsewhere.
        return {"kind": "no_claude", "transcripts": str(transcripts)}
    # One file is enough to tell "there is history" from "there is none": the tree can
    # hold hundreds of megabytes, and counting it all here would be paid for on a page
    # that is only ever shown when the base is empty anyway.
    has_history = next(transcripts.rglob("*.jsonl"), None) is not None
    kind = "not_indexed" if has_history else "no_history"
    return {"kind": kind, "transcripts": str(transcripts)}


def data_stamps(conn: sqlite3.Connection, now: datetime) -> dict[str, str | None]:
    """The time of the freshest event in each slice of the overview.

    The widget shows not the moment of recomputation (that happens every second and
    without new turns) but the time of the data it stands on: in a pause the mark honestly
    freezes. The slices differ, so the times differ too: the feed has the last turn of all,
    the daily widgets have the last turn since local midnight.
    """
    row = conn.execute(
        """
        SELECT (SELECT MAX(ts) FROM turns)                       AS last_turn,
               (SELECT MAX(ts) FROM turns WHERE ts >= :day)      AS today_turn,
               (SELECT MAX(t.ts) FROM tool_calls AS c
                  JOIN turns AS t ON t.id = c.turn_id
                 WHERE t.ts >= :day)                             AS tool_call,
               (SELECT MAX(ts) FROM turns
                 WHERE ts >= :day AND output_tokens < :out
                   AND context_estimate > :ctx)                  AS idle_turn
        """,
        {
            "day": _utc_stamp(local_day_start(now)),
            "out": IDLE_MAX_OUTPUT,
            "ctx": IDLE_MIN_CONTEXT,
        },
    ).fetchone()
    return dict(row)


def recent_turns(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    """A feed of the latest turns with the tools they called."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.message_id, t.session_id, t.ts, t.model, t.output_tokens,
                   t.input_tokens, t.cache_read, t.is_sidechain,
                   t.cache_write_5m + t.cache_write_1h AS cache_write,
                   t.context_estimate, COALESCE(p.display_name, p.slug) AS project,
                   (SELECT GROUP_CONCAT(c.tool, ' ') FROM tool_calls AS c
                     WHERE c.turn_id = t.id) AS tools
              FROM turns AS t
              LEFT JOIN sessions AS s ON s.id = t.session_id
              LEFT JOIN projects AS p ON p.id = s.project_id
             ORDER BY t.ts DESC, t.id DESC LIMIT ?
            """,
            (limit,),
        )
    ]


#: The chart recorder step. Finer than the limit is impossible: Claude Code appends the
#: transcript in bursts every 2-6 seconds, and a turn's spend is known only once it
#: finishes, so at two seconds bursts already separate into empty buckets.
SERIES_BUCKET_SECONDS = 2
SERIES_SPAN_MINUTES = 5


def burn_series(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    bucket_seconds: int = SERIES_BUCKET_SECONDS,
    span_minutes: int = SERIES_SPAN_MINUTES,
) -> list[dict]:
    """Spend by time buckets - the chart recorder tape of the last minutes.

    Empty buckets are filled with zeros: without them a gap in work would look like
    a continuous load with rare points.
    """
    start = now - timedelta(minutes=span_minutes)
    edge = int(start.timestamp()) // bucket_seconds * bucket_seconds
    last = int(now.timestamp()) // bucket_seconds * bucket_seconds
    filled = {
        int(row["bucket"]): row
        for row in conn.execute(
            """
            SELECT CAST(strftime('%s', ts) AS INTEGER) / :step * :step AS bucket,
                   COUNT(*)                                            AS turns,
                   SUM(output_tokens)                                  AS output_tokens,
                   SUM(input_tokens + output_tokens + cache_read
                       + cache_write_5m + cache_write_1h)              AS tokens
              FROM turns
             WHERE ts >= :since
             GROUP BY bucket
            """,
            {"step": bucket_seconds, "since": _utc_stamp(start)},
        )
    }
    series: list[dict] = []
    for bucket in range(edge, last + bucket_seconds, bucket_seconds):
        row = filled.get(bucket)
        series.append(
            {
                "at": datetime.fromtimestamp(bucket, UTC).isoformat(),
                "turns": row["turns"] if row else 0,
                "tokens": row["tokens"] if row else 0,
                "output_tokens": row["output_tokens"] if row else 0,
            }
        )
    return series


def pending_sessions(conn: sqlite3.Connection, now: datetime, minutes: int = 10) -> list[str]:
    """Sessions where the model is working right now (SPEC §4).

    The tokens of such a request are not known yet: they show up in the transcript only
    together with the finished turn.
    """
    return [
        session["id"] for session in live_sessions(conn, now) if session["status"] == STATUS_WORKING
    ]


def set_hidden(conn: sqlite3.Connection, session_id: str, hidden: bool) -> bool:
    """Remove a session from the dashboard or bring it back. Nothing changes in the transcripts."""
    with conn:
        cursor = conn.execute(
            "UPDATE sessions SET hidden = ? WHERE id = ?", (int(hidden), session_id)
        )
    return cursor.rowcount > 0


# --- SPEC §4 metrics (task B3) -----------------------------------------------

#: An idle turn: the model answered almost nothing although the context is already large -
#: the "waiting" case from the report (SPEC §4). The thresholds live here rather than in the
#: config: this is the definition of a metric, not a setting.
IDLE_MAX_OUTPUT = 10
IDLE_MIN_CONTEXT = 50_000

#: The subscription limit window: 5 hours from the first turn of a series. Claude Code
#: does not write the starting point into the transcript, so the window is reconstructed
#: from the data and marked as an approximation (refined over OTel - milestone E).
LIMIT_WINDOW_HOURS = 5
WEEK_HOURS = 24 * 7


def model_share(
    conn: sqlite3.Connection, since: datetime, project: str | None = None
) -> list[dict]:
    """Model share over the period: turns and tokens (SPEC §4)."""
    clause, params = project_filter(project)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT COALESCE(model, '-')                      AS model,
                   COUNT(*)                                  AS turns,
                   COALESCE(SUM(output_tokens), 0)           AS output_tokens,
                   COALESCE(SUM(input_tokens + output_tokens + cache_read
                                + cache_write_5m + cache_write_1h), 0) AS tokens
              FROM turns WHERE ts >= ?{clause}
             GROUP BY model ORDER BY tokens DESC
            """,  # noqa: S608
            (_utc_stamp(since), *params),
        )
    ]


def tool_profile(
    conn: sqlite3.Connection, since: datetime, limit: int = 8, project: str | None = None
) -> dict:
    """The tool profile over the period; inside Bash - by normalised commands."""
    clause, params = project_filter(project, "t.session_id")
    tools = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT c.tool, COUNT(*) AS calls
              FROM tool_calls AS c JOIN turns AS t ON t.id = c.turn_id
             WHERE t.ts >= ?{clause}
             GROUP BY c.tool ORDER BY calls DESC
            """,  # noqa: S608
            (_utc_stamp(since), *params),
        )
    ]
    commands = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT c.detail AS command, COUNT(*) AS calls
              FROM tool_calls AS c JOIN turns AS t ON t.id = c.turn_id
             WHERE t.ts >= ? AND c.tool = 'Bash' AND c.detail IS NOT NULL{clause}
             GROUP BY c.detail ORDER BY calls DESC LIMIT ?
            """,  # noqa: S608
            (_utc_stamp(since), *params, limit),
        )
    ]
    return {
        "tools": tools[:limit],
        "tools_total": sum(row["calls"] for row in tools),
        "bash_commands": commands,
    }


def idle_turns(conn: sqlite3.Connection, since: datetime, project: str | None = None) -> dict:
    """Idle turns over the period: how many and what they cost."""
    clause, params = project_filter(project)
    row = conn.execute(
        f"""
        SELECT COUNT(*)                        AS turns,
               COALESCE(SUM(cache_read), 0)    AS cache_read,
               COALESCE(SUM(output_tokens), 0) AS output_tokens
          FROM turns
         WHERE ts >= ? AND output_tokens < ? AND context_estimate > ?{clause}
        """,  # noqa: S608
        (_utc_stamp(since), IDLE_MAX_OUTPUT, IDLE_MIN_CONTEXT, *params),
    ).fetchone()
    total = conn.execute(
        f"SELECT COUNT(*) FROM turns WHERE ts >= ?{clause}",  # noqa: S608
        (_utc_stamp(since), *params),
    ).fetchone()[0]
    idle = dict(row)
    idle["share"] = idle["turns"] / total if total else 0.0
    idle["max_output"] = IDLE_MAX_OUTPUT
    idle["min_context"] = IDLE_MIN_CONTEXT
    return idle


def limit_window(conn: sqlite3.Connection, now: datetime) -> dict:
    """An estimate of the subscription limit window - an approximation (SPEC §4).

    Claude Code writes neither the window bounds nor the limits themselves into the
    transcript, so the window is reconstructed from the turns: it starts with the first
    turn after a pause longer than five hours and lasts just as long. We count the spend
    inside the current window and over a rolling week; "how much is left" cannot be said
    without the limits, so we return volume rather than percentages.
    """
    window_start = _current_window_start(conn, now)
    window = window_usage(conn, window_start) if window_start else None
    week = window_usage(conn, now - timedelta(hours=WEEK_HOURS))
    return {
        "approximate": True,
        "window_hours": LIMIT_WINDOW_HOURS,
        "started_at": window_start.isoformat() if window_start else None,
        "resets_at": (
            (window_start + timedelta(hours=LIMIT_WINDOW_HOURS)).isoformat()
            if window_start
            else None
        ),
        "usage": window,
        "week": week,
    }


def _current_window_start(conn: sqlite3.Connection, now: datetime) -> datetime | None:
    """The start of the current five-hour window: the first turn after a pause longer than it."""
    rows = conn.execute(
        """
        SELECT ts FROM turns
         WHERE ts >= ? ORDER BY ts
        """,
        (_utc_stamp(now - timedelta(hours=LIMIT_WINDOW_HOURS * 4)),),
    ).fetchall()
    if not rows:
        return None
    span = timedelta(hours=LIMIT_WINDOW_HOURS)
    start = _parse_stamp(rows[0]["ts"])
    for row in rows[1:]:
        moment = _parse_stamp(row["ts"])
        if moment - start >= span:  # the previous window closed, a new one began
            start = moment
    return start


def _parse_stamp(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
