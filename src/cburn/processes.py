"""Finding Claude Code processes (the "close session" button).

The exact `sessionId` -> pid link comes from Claude Code itself: `claude agents --json`
prints the active sessions, interactive ones included. The `sessionId` cannot be found
in the process itself - it is neither in the arguments nor in the open descriptors: the
transcript is appended to and closed right away.

On being busy. A running tool cannot be told from a hanging permission request by the
transcript: in both cases the last record is a tool request without an answer. The
difference shows up in processes: a long Bash is a live child of the session process,
while on a "allow?" question the process simply waits for the human. Permanent children
(MCP servers) start together with the session, so we look at the youngest one: whether it
belongs to the current request is decided by `metrics`.

On termination. Claude Code has no signal handler of its own: registered
`SIGINT`/`SIGHUP`/`SIGTERM` cause an immediate `process.exit()`, while `SessionEnd`
hooks run asynchronously on a regular exit (`/exit`, Ctrl+D, `/clear`, logout).
So SIGTERM closes the session, but the `SessionEnd` hooks most likely will not run -
the dashboard warns about that honestly. There is no "close someone else's session"
command in the CLI.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

#: Running the binary is slow (about 1.3 s), so the list is cached.
TIMEOUT = 30.0

#: How long the session list cache lives: sessions do not appear more often than that.
CACHE_SECONDS = 15.0

CLAUDE_BINARY = "claude"

#: (moment of the request, answer). None in the answer means asking failed.
_cache: tuple[float, list[ClaudeSession] | None] = (0.0, None)


@dataclass(frozen=True)
class ClaudeSession:
    """An active Claude Code session as Claude Code itself sees it."""

    pid: int
    session_id: str
    cwd: str | None = None
    kind: str | None = None
    name: str | None = None


def active_sessions(*, use_cache: bool = False) -> list[ClaudeSession]:
    """Ask Claude Code for the list of its sessions.

    An empty list means both "no sessions" and "asking failed". Where the difference
    matters, use `active_session_ids`.
    """
    return _ask(use_cache=use_cache) or []


def live_state(*, use_cache: bool = True) -> dict[str, datetime | None] | None:
    """Live sessions and the start moment of the youngest child of each.

    None instead of a dict means asking failed; telling that apart from "no sessions"
    is mandatory: a silent `claude` is no reason to declare every session finished.
    None for a session means no children, that is, the process runs nothing.
    """
    sessions = _ask(use_cache=use_cache)
    if sessions is None:
        return None
    starts = youngest_children({session.pid for session in sessions})
    return {session.session_id: starts.get(session.pid) for session in sessions}


def youngest_children(pids: Iterable[int]) -> dict[int, datetime]:
    """The start moment of the latest direct child for each of `pids`.

    Processes without children do not show up in the answer. The age comes from `ps` (`etime`),
    not from the start time (`lstart`): the `etime` format does not depend on the locale.
    """
    wanted = set(pids)
    if not wanted:
        return {}
    now = datetime.now(UTC)
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,etime="],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not get the process list: %s", exc)
        return {}
    starts: dict[int, datetime] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        parent = int(parts[1])
        age = _parse_etime(parts[2])
        if parent not in wanted or age is None:
            continue
        started = now - age
        if started > starts.get(parent, started - timedelta(seconds=1)):
            starts[parent] = started
    return starts


def _parse_etime(value: str) -> timedelta | None:
    """`[[dd-]hh:]mm:ss` from `ps` into a duration."""
    days, _, clock = value.rpartition("-")
    chunks = clock.split(":")
    if not all(chunk.isdigit() for chunk in chunks) or not 2 <= len(chunks) <= 3:
        return None
    if days and not days.isdigit():
        return None
    hours, minutes, seconds = ([0] * (3 - len(chunks))) + [int(chunk) for chunk in chunks]
    return timedelta(days=int(days or 0), hours=hours, minutes=minutes, seconds=seconds)


def _ask(*, use_cache: bool) -> list[ClaudeSession] | None:
    global _cache
    asked_at, cached = _cache
    if use_cache and cached is not None and time.monotonic() - asked_at < CACHE_SECONDS:
        return cached
    sessions = _run()
    if sessions is not None:
        _cache = (time.monotonic(), sessions)
    return sessions


def _run() -> list[ClaudeSession] | None:
    try:
        result = subprocess.run(
            [CLAUDE_BINARY, "agents", "--json"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not get the Claude Code session list: %s", exc)
        return None
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        log.warning("could not parse the Claude Code session list: %s", exc)
        return None
    sessions = []
    for row in rows if isinstance(rows, list) else []:
        pid, session_id = row.get("pid"), row.get("sessionId")
        if isinstance(pid, int) and isinstance(session_id, str):
            sessions.append(
                ClaudeSession(
                    pid=pid,
                    session_id=session_id,
                    cwd=row.get("cwd"),
                    kind=row.get("kind"),
                    name=row.get("name"),
                )
            )
    return sessions


def process_for_session(session_id: str) -> ClaudeSession | None:
    """The session process by its id. The cache is not used: we close by it."""
    return next((s for s in active_sessions() if s.session_id == session_id), None)


def terminate(pid: int) -> bool:
    """Ask the process to finish (SIGTERM). We never kill by force."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as exc:
        log.warning("could not terminate process %s: %s", pid, exc)
        return False
    return True
