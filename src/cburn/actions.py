"""Carrying a tip out: a typed action, a diff, a confirmation, a rollback (task D7).

The advisor no longer only writes what to do: next to the text it returns an `act` from
a closed list, and the dashboard can carry it out. Nothing happens by itself - the plan
is built first, the human sees the real diff of the file and confirms it, and only then
does the write happen.

The "`~/.claude` is read-only" invariant is narrowed rather than dropped:

* the transcripts (`~/.claude/projects`) stay untouched - they are our data source, and
  a write there would move the very offsets we read the tail from;
* `~/.claude.json` stays untouched - Claude Code rewrites it every few seconds, so a
  read-modify-write of ours would silently lose someone else's change;
* `settings.json` (the user one) and `settings.local.json` (the project one) may be
  written, and only through this module. The project file is the personal one on purpose:
  `settings.json` inside a repository is committed, and we do not touch a tracked file.

Every write leaves a copy in `~/.local/share/cburn/backups/` and a row in
`applied_patches`: a change in a foreign config without a way back is not a change,
it is a loss. The confirmed diff is stored next to it - that is what the human agreed to.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import shutil
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import metrics, paths
from .processes import process_for_session, terminate

log = logging.getLogger(__name__)

CLOSE_SESSION = "close_session"
ALLOW_PERMISSION = "allow_permission"
DISABLE_HOOK = "disable_hook"
DISABLE_PLUGIN = "disable_plugin"

#: The whole closed list. The model picks a type from it and fills the parameters; it
#: never writes file contents itself - what to change in the file is decided here.
ACT_TYPES = (CLOSE_SESSION, ALLOW_PERMISSION, DISABLE_HOOK, DISABLE_PLUGIN)

#: Without these an action means nothing and is dropped along with the act.
REQUIRED: dict[str, tuple[str, ...]] = {
    CLOSE_SESSION: ("session_id",),
    ALLOW_PERMISSION: ("rule",),
    DISABLE_HOOK: ("event",),
    DISABLE_PLUGIN: ("plugin",),
}

#: Everything else the model may send is thrown away: the act is executed, so it goes
#: through the narrowest gate there is.
OPTIONAL: dict[str, tuple[str, ...]] = {
    CLOSE_SESSION: (),
    ALLOW_PERMISSION: ("scope", "project"),
    DISABLE_HOOK: ("matcher", "scope", "project"),
    DISABLE_PLUGIN: ("scope", "project"),
}

#: The act inside an advisor answer. The description sits in the prompt: a schema is
#: read by the machine, and the model needs the rules in words.
ACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(ACT_TYPES)},
        "session_id": {"type": "string"},
        "rule": {"type": "string"},
        "event": {"type": "string"},
        "matcher": {"type": "string"},
        "plugin": {"type": "string"},
        "scope": {"type": "string", "enum": ["user", "project"]},
        "project": {"type": "string"},
    },
    "required": ["type"],
    "additionalProperties": False,
}

PENDING = "pending"
APPLIED = "applied"
ROLLED_BACK = "rolled_back"
FAILED = "failed"

#: Where the backups live. Not next to the original: a stray file inside `~/.claude`
#: would be exactly the write we promised not to make.
BACKUP_DIR = "backups"


class ActError(Exception):
    """A refusal with a reason the screen can word itself.

    `reason` is a dictionary key rather than a sentence: the interface has two languages
    and the server has none.
    """

    def __init__(self, reason: str, **extra: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.extra = extra


@dataclass(frozen=True)
class Plan:
    """What exactly would happen. Built before the confirmation and once more after it."""

    kind: str
    #: The file to be written, or the session to be closed.
    target: str
    #: Data for the screen (a rule, an event, a session): words are added by the frontend.
    details: dict[str, Any] = field(default_factory=dict)
    #: A unified diff of the file, empty where nothing is written.
    diff: str = ""
    #: The state the plan was built from. A foreign change in between invalidates it.
    before_hash: str = ""
    after_text: str = ""
    after_hash: str = ""
    #: Dictionary keys for the warnings under the diff (SIGTERM, a restart is needed).
    notes: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        """What goes to the screen: the file contents themselves are not needed there."""
        return {
            "kind": self.kind,
            "target": self.target,
            "details": self.details,
            "diff": self.diff,
            "hash": self.before_hash,
            "notes": list(self.notes),
        }


def enabled(config: Mapping[str, Any] | None) -> bool:
    """The switch for the whole door (`actions.enabled`)."""
    return bool(((config or {}).get("actions") or {}).get("enabled", True))


def normalise(act: Any) -> dict[str, Any] | None:
    """An act from the model into ours, or `None` if it is not one of the known ones."""
    if not isinstance(act, dict):
        return None
    kind = act.get("type")
    if kind not in ACT_TYPES:
        return None
    clean: dict[str, Any] = {"type": kind}
    for name in REQUIRED[kind] + OPTIONAL[kind]:
        value = act.get(name)
        if isinstance(value, str) and value.strip():
            clean[name] = value.strip()
    if any(name not in clean for name in REQUIRED[kind]):
        log.info("an act without its parameters was dropped: %s", kind)
        return None
    return clean


# --------------------------------------------------------------------------- planning


def plan(conn: sqlite3.Connection, act: Mapping[str, Any]) -> Plan:
    """Work out what the act would change. Nothing is written here."""
    kind = act.get("type")
    if kind == CLOSE_SESSION:
        return _plan_close(conn, act)
    if kind in (ALLOW_PERMISSION, DISABLE_HOOK, DISABLE_PLUGIN):
        return _plan_settings(conn, act)
    raise ActError("unknown_act")


def _plan_close(conn: sqlite3.Connection, act: Mapping[str, Any]) -> Plan:
    """Closing a session: no file is written, the process gets a signal in a pause."""
    row = _session_row(conn, str(act["session_id"]))
    if row is None:
        raise ActError("not_found")
    status = metrics.session_status(row, datetime.now(UTC))
    notes = ["sigterm"]
    if row.get("is_live") == 0:
        notes.append("not_live")
    elif status == metrics.STATUS_WORKING:
        notes.append("waits_for_idle")
    return Plan(
        kind=CLOSE_SESSION,
        target=str(row["id"]),
        details={
            "session_id": row["id"],
            "project": row["project"],
            "status": status,
            "live": row.get("is_live") == 1,
        },
        notes=tuple(notes),
    )


def _plan_settings(conn: sqlite3.Connection, act: Mapping[str, Any]) -> Plan:
    """A patch of a settings file: the diff is a real one, over the text we would write."""
    path = _settings_path(conn, act)
    before = _read(path)
    try:
        data = json.loads(before) if before.strip() else {}
    except json.JSONDecodeError as exc:
        raise ActError("unreadable") from exc
    if not isinstance(data, dict):
        raise ActError("unreadable")

    kind = str(act["type"])
    details = _mutate(data, act)
    after = _dumps(data)
    if after == before:
        raise ActError("no_change")
    return Plan(
        kind=kind,
        target=str(path),
        details=details | {"path": str(path)},
        diff=_diff(before, after, path),
        before_hash=_hash(before),
        after_text=after,
        after_hash=_hash(after),
        notes=("restart_needed",),
    )


def _mutate(data: dict[str, Any], act: Mapping[str, Any]) -> dict[str, Any]:
    """Change the settings in place; return the data the screen names the act by."""
    kind = act["type"]
    if kind == ALLOW_PERMISSION:
        rule = str(act["rule"])
        allow = data.setdefault("permissions", {}).setdefault("allow", [])
        if not isinstance(allow, list):
            raise ActError("unreadable")
        if rule in allow:
            raise ActError("no_change")
        allow.append(rule)
        return {"rule": rule}

    if kind == DISABLE_HOOK:
        event, matcher = str(act["event"]), act.get("matcher")
        hooks = data.get("hooks")
        if not isinstance(hooks, dict) or event not in hooks:
            raise ActError("not_found")
        if matcher is None:
            hooks.pop(event)
        else:
            groups = hooks.get(event)
            kept = [
                group
                for group in (groups if isinstance(groups, list) else [])
                if not (isinstance(group, dict) and group.get("matcher") == matcher)
            ]
            if isinstance(groups, list) and len(kept) == len(groups):
                raise ActError("not_found")
            hooks[event] = kept
            if not kept:
                hooks.pop(event)
        if not hooks:
            data.pop("hooks")
        return {"event": event, "matcher": matcher}

    if kind == DISABLE_PLUGIN:
        plugins = data.get("enabledPlugins")
        if not isinstance(plugins, dict):
            raise ActError("not_found")
        name = _plugin_key(plugins, str(act["plugin"]))
        if name is None:
            raise ActError("not_found")
        if plugins[name] is False:
            raise ActError("no_change")
        # Switched off rather than removed: the entry stays visible in the file, the diff
        # is one line, and switching it back on is a matter of the same one line.
        plugins[name] = False
        return {"plugin": name}

    raise ActError("unknown_act")


def _plugin_key(plugins: Mapping[str, Any], wanted: str) -> str | None:
    """The full plugin key by its name: the model sees `playwright`, the file `name@source`."""
    if wanted in plugins:
        return wanted
    matches = [name for name in plugins if name.split("@", 1)[0] == wanted]
    return matches[0] if len(matches) == 1 else None


def _settings_path(conn: sqlite3.Connection, act: Mapping[str, Any]) -> Path:
    """Which file the act edits.

    The project scope points at `settings.local.json`: `settings.json` inside a repository
    is committed, and a machine-local decision has no business in someone else's history.
    """
    if act.get("scope") != "project":
        return paths.CLAUDE_DIR / "settings.json"
    name = act.get("project")
    if not name:
        raise ActError("no_project")
    row = conn.execute(
        "SELECT root_path FROM projects WHERE COALESCE(display_name, slug) = ? AND"
        " root_path IS NOT NULL LIMIT 1",
        (name,),
    ).fetchone()
    if row is None:
        raise ActError("no_project")
    return Path(str(row["root_path"])) / ".claude" / "settings.local.json"


# --------------------------------------------------------------------------- applying


def apply(
    conn: sqlite3.Connection,
    act: Mapping[str, Any],
    *,
    before_hash: str,
    item_id: int | None = None,
) -> dict[str, Any]:
    """Carry the act out after the confirmation. `before_hash` is what the human saw.

    The plan is rebuilt here rather than carried over from the preview: between showing
    the diff and pressing the button Claude Code may have rewritten the file itself, and
    a stale plan would wipe that change out.
    """
    fresh = plan(conn, act)
    if fresh.before_hash != before_hash:
        raise ActError("stale")

    if fresh.kind == CLOSE_SESSION:
        # The signal is not sent from here: the session may be in the middle of a step.
        # The liveness pass finds the record and closes it in a pause (`run_pending`).
        patch_id = _record(conn, fresh, act, item_id, status=PENDING)
        return {"patch_id": patch_id, "status": PENDING} | fresh.public()

    path = Path(fresh.target)
    backup = _backup(path)
    try:
        _atomic_write(path, fresh.after_text)
    except OSError as exc:
        log.warning("could not write %s: %s", path, exc)
        _record(conn, fresh, act, item_id, status=FAILED, backup=backup)
        raise ActError("write_failed") from exc
    patch_id = _record(conn, fresh, act, item_id, status=APPLIED, backup=backup)
    log.info("act %s applied: %s", fresh.kind, path)
    return {"patch_id": patch_id, "status": APPLIED} | fresh.public()


def rollback(conn: sqlite3.Connection, patch_id: int) -> dict[str, Any]:
    """Put back what we changed - unless a foreign change has appeared since."""
    row = conn.execute("SELECT * FROM applied_patches WHERE id = ?", (patch_id,)).fetchone()
    if row is None:
        raise ActError("not_found")
    if row["status"] == ROLLED_BACK:
        raise ActError("already_rolled_back")

    if row["kind"] == CLOSE_SESSION:
        # A pending close is simply cancelled; a process already terminated does not come back.
        if row["status"] != PENDING:
            raise ActError("no_rollback")
        _set_status(conn, patch_id, ROLLED_BACK)
        return {"patch_id": patch_id, "status": ROLLED_BACK}

    if row["status"] != APPLIED:
        raise ActError("no_rollback")
    path = Path(str(row["target"]))
    current = _read(path)
    # What lies there now must be what we wrote. Otherwise the file has been edited since -
    # by a human or by Claude Code - and restoring the copy would throw that away.
    if row["after_hash"] and _hash(current) != row["after_hash"]:
        raise ActError("changed_since")
    try:
        if row["backup"]:
            _atomic_write(path, _read(Path(str(row["backup"]))))
        else:
            path.unlink(missing_ok=True)  # the file did not exist before us
    except OSError as exc:
        raise ActError("write_failed") from exc
    _set_status(conn, patch_id, ROLLED_BACK)
    log.info("patch %s rolled back: %s", patch_id, path)
    return {"patch_id": patch_id, "status": ROLLED_BACK}


def run_pending(
    conn: sqlite3.Connection,
    live: Mapping[str, datetime | None] | None,
    *,
    now: datetime | None = None,
) -> int:
    """Close the sessions the human agreed to close, in a pause between steps.

    Called from the liveness pass: it already knows which sessions are alive and what
    their processes are doing. A working session is left alone until the next pass - that
    is the whole difference from the "close" button, which fires at once.
    """
    rows = conn.execute(
        "SELECT id, target FROM applied_patches WHERE kind = ? AND status = ?",
        (CLOSE_SESSION, PENDING),
    ).fetchall()
    if not rows:
        return 0
    now = now or datetime.now(UTC)
    closed = 0
    for row in rows:
        session_id = str(row["target"])
        state = _session_row(conn, session_id)
        if state is None:
            _set_status(conn, int(row["id"]), FAILED, note="not_found")
            continue
        # `live=None` means the poll failed: a stale liveness is no reason to send a signal.
        if live is not None and session_id not in live:
            _set_status(conn, int(row["id"]), APPLIED, note="already_gone")
            metrics.set_hidden(conn, session_id, True)
            closed += 1
            continue
        if metrics.session_status(state, now) == metrics.STATUS_WORKING:
            continue  # a step is running - we wait for the pause
        process = process_for_session(session_id)
        if process is None:
            _set_status(conn, int(row["id"]), APPLIED, note="already_gone")
        else:
            _set_status(
                conn,
                int(row["id"]),
                APPLIED if terminate(process.pid) else FAILED,
                note="terminated",
            )
        metrics.set_hidden(conn, session_id, True)
        closed += 1
    return closed


def history(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """What has been carried out: for the "Advice" screen and for `cburn stats`."""
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id, ts, item_id, kind, target, status, note, diff,"
            " backup IS NOT NULL AS has_backup"
            " FROM applied_patches ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    ]


def patches_for_items(conn: sqlite3.Connection, item_ids: list[int]) -> dict[int, dict[str, Any]]:
    """The latest patch of every tip: the card shows whether it has been carried out."""
    if not item_ids:
        return {}
    holes = ",".join("?" * len(item_ids))
    latest: dict[int, dict[str, Any]] = {}
    for row in conn.execute(
        f"SELECT id, item_id, kind, status, ts, note FROM applied_patches"  # noqa: S608
        f" WHERE item_id IN ({holes}) ORDER BY id",
        tuple(item_ids),
    ):
        latest[int(row["item_id"])] = dict(row)
    return latest


# --------------------------------------------------------------------------- the plumbing


def _session_row(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    """The session state in the shape `metrics.session_status` expects.

    The id comes from the advisor in the short form (the digest carries no other), so
    a prefix is enough to find it.
    """
    row = conn.execute(
        f"""
        SELECT s.id, COALESCE(p.display_name, p.slug) AS project, s.is_live, s.busy_since,
               s.last_at, s.last_record_kind, s.last_record_at, s.last_stop_reason,
               {metrics.OTEL_SESSION_COLUMNS}
               s.hidden
          FROM sessions AS s
          LEFT JOIN projects AS p ON p.id = s.project_id
         WHERE s.id = ? OR s.id LIKE ? || '%'
         ORDER BY s.last_at DESC LIMIT 1
        """,  # noqa: S608
        (session_id, session_id),
    ).fetchone()
    return dict(row) if row is not None else None


def _record(
    conn: sqlite3.Connection,
    plan: Plan,
    act: Mapping[str, Any],
    item_id: int | None,
    *,
    status: str,
    backup: Path | None = None,
) -> int:
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO applied_patches
                (ts, item_id, kind, act_json, target, diff, backup, after_hash, status)
            VALUES (:ts, :item_id, :kind, :act, :target, :diff, :backup, :after_hash, :status)
            """,
            {
                "ts": datetime.now(UTC).isoformat(),
                "item_id": item_id,
                "kind": plan.kind,
                "act": json.dumps(dict(act), ensure_ascii=False),
                "target": plan.target,
                "diff": plan.diff,
                "backup": str(backup) if backup else None,
                "after_hash": plan.after_hash,
                "status": status,
            },
        )
    return int(cursor.lastrowid or 0)


def _set_status(
    conn: sqlite3.Connection, patch_id: int, status: str, note: str | None = None
) -> None:
    with conn:
        conn.execute(
            "UPDATE applied_patches SET status = ?, note = COALESCE(?, note) WHERE id = ?",
            (status, note, patch_id),
        )


def _backup(path: Path) -> Path | None:
    """A copy to restore from. `None` means there was no file - a rollback deletes ours."""
    if not path.exists():
        return None
    directory = paths.DATA_DIR / BACKUP_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    target = directory / f"{stamp}-{path.parent.name}-{path.name}"
    # A second act within the same second must not overwrite the first copy.
    index = 1
    while target.exists():
        target = target.with_name(f"{stamp}-{index}-{path.parent.name}-{path.name}")
        index += 1
    shutil.copy2(path, target)
    return target


def _atomic_write(path: Path, text: str) -> None:
    """Write through a temporary file next to the target: a half-written config is worse
    than none at all. The mode of the original is kept - `settings.json` may hold tokens."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    temporary = path.with_name(f".{path.name}.cburn-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise ActError("unreadable") from exc


def _dumps(data: Any) -> str:
    """The same shape the file is written in: two spaces, live characters, a final newline."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _diff(before: str, after: str, path: Path) -> str:
    """A unified diff of the file. It is never logged: `settings.json` holds hook tokens."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
            n=3,
        )
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
