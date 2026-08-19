"""When it is worth writing to telegram and when to stay quiet (task D5, SPEC §7).

The decisions live in pure functions: network and database stay outside, and the rules
are covered by tests without a single request. There are three rules and all of them
are about one thing - not waking the human without a reason:

* the hourly summary goes out only if the advisor found something above `info`;
* the daily digest goes once a day at the appointed time, and only once;
* an instant per-session alert goes no more often than once per `COOLDOWN`.

On top of all that sits the global pause: two hours of silence that only `crit`
overrides - when the spend is burning right now, staying quiet is not an option.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: How long a particular session goes quiet after its alert. Half an hour is roughly
#: the length of one work stretch: there is nothing to remind about more often.
COOLDOWN = timedelta(minutes=30)

#: How long the global pause from the button lasts (SPEC §5).
PAUSE = timedelta(hours=2)

#: Severity order: comparison follows this list, not the alphabet.
SEVERITY = ("info", "warn", "crit")


def rank(severity: str | None) -> int:
    """Place on the severity scale; an unknown word counts as the mildest."""
    try:
        return SEVERITY.index(severity or "info")
    except ValueError:
        return 0


@dataclass(frozen=True)
class Message:
    """A ready message: the text is already built, the channel only has to send it."""

    kind: str  # digest | daily | alert
    text: str
    severity: str = "info"
    #: What the memory of this message is keyed by: the session for an alert, the date for a digest.
    key: str | None = None


def is_paused(paused_until: datetime | None, now: datetime) -> bool:
    return paused_until is not None and paused_until > now


def allowed(message: Message, paused_until: datetime | None, now: datetime) -> bool:
    """Whether to let the message through the pause.

    `crit` always passes: the pause means "do not bother me with small things", not
    "switch the instrument off".
    """
    return message.severity == "crit" or not is_paused(paused_until, now)


def digest_message(severity: str | None, summary: str) -> Message | None:
    """The advisor's hourly summary - only if there is something to talk about."""
    if rank(severity) < rank("warn"):
        return None
    return Message(kind="digest", text=summary, severity=severity or "warn")


def daily_message(
    now: datetime,
    at: str,
    last_daily: datetime | None,
    summary: str,
) -> Message | None:
    """The daily digest: once a day, after the appointed time.

    The time is compared in local hours - a human reads "21:00" as evening where they
    are, not in UTC. If the dashboard was switched off, the digest goes out on the first
    start after the deadline, but still only once a day.
    """
    local = now.astimezone()
    hours, _, minutes = at.partition(":")
    try:
        due = local.replace(hour=int(hours), minute=int(minutes), second=0, microsecond=0)
    except ValueError:
        return None
    if local < due:
        return None
    if last_daily is not None and last_daily.astimezone() >= due:
        return None
    return Message(kind="daily", text=summary, severity="info", key=local.date().isoformat())


def alert_messages(
    now: datetime,
    candidates: list[tuple[str, str, str]],
    last_alerts: dict[str, datetime],
) -> list[Message]:
    """Instant alerts with a per-session cooldown.

    `candidates` are triples of "key, severity, text": the rules for what counts as a
    reason live in the calling code, here we only keep the memory of whom we have
    bothered recently.
    """
    fresh: list[Message] = []
    for key, severity, text in candidates:
        last = last_alerts.get(key)
        if last is not None and now - last < COOLDOWN:
            continue
        fresh.append(Message(kind="alert", text=text, severity=severity, key=key))
    return fresh


def pause_until(now: datetime | None = None) -> datetime:
    """Until when to stay quiet after "pause for 2 hours" was pressed."""
    return (now or datetime.now(UTC)) + PAUSE
