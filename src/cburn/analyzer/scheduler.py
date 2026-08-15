"""Advisor scheduler (task D3, TZ §6).

A tick every `analyzer.interval_minutes`, and once a week a deep analysis on a bigger
model. Every tick costs money, so the "call the model or not" decision lives in the pure
function `plan_tick`: it is visible in tests, and it stays quiet when there is nothing
to advise about.

We skip a tick when:

* the advisor is switched off in the config;
* the interval has not passed since the previous tick;
* there was not a single turn in the period - nothing to analyse.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import metrics, notifier
from . import advisor, digest

log = logging.getLogger(__name__)

HOURLY = "hourly"
WEEKLY = "weekly"
#: An analysis started by a human: from the CLI or by the button on the "Advice" screen.
#: It affects the schedule the same way an hourly one does - the interval counts from any
#: tick - but it must be labelled honestly in the history.
MANUAL = "manual"

#: A week between deep analyses. Counted from the previous weekly tick, not by the
#: calendar: a server restart must not shift the schedule.
WEEKLY_PERIOD = timedelta(days=7)

#: How long to wait after start before the first tick. The server gets restarted often, and
#: a tick on every start is money spent for nothing.
WARMUP = timedelta(minutes=5)


@dataclass(frozen=True)
class Tick:
    """What to analyse and over which period."""

    kind: str
    since: datetime
    model: str


def plan_tick(
    conn: sqlite3.Connection,
    now: datetime,
    config: dict[str, Any],
    *,
    started_at: datetime | None = None,
) -> Tick | None:
    """Decide whether a tick is due right now. `None` means skip."""
    analyzer = config.get("analyzer") or {}
    if not analyzer.get("enabled", True):
        return None
    if started_at is not None and now - started_at < WARMUP:
        return None

    interval = timedelta(minutes=float(analyzer.get("interval_minutes") or 60))
    # The interval counts from any tick, not only from an hourly one: the weekly
    # analysis has just looked at the same data, repeating it for $0.08 is pointless.
    last_any = _last_tick(conn)
    last_weekly = _last_tick(conn, WEEKLY)

    if last_weekly is None or now - last_weekly >= WEEKLY_PERIOD:
        since = last_weekly or now - WEEKLY_PERIOD
        if _has_turns(conn, since, now):
            return Tick(WEEKLY, since, str(analyzer.get("weekly_deep_model") or "sonnet"))

    if last_any is not None and now - last_any < interval:
        return None
    since = last_any or now - interval
    if not _has_turns(conn, since, now):
        log.debug("tick skipped: no turns in the period")
        return None
    return Tick(HOURLY, since, str(analyzer.get("model") or "haiku"))


def run_tick(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    tick: Tick,
    *,
    runner: Any = None,
) -> dict[str, Any]:
    """Build the digest for the tick period and run it through the advisor."""
    payload = digest.build(conn, tick.since, config=config)
    result = advisor.advise(conn, payload, model=tick.model, runner=runner, kind=tick.kind)
    result["kind"] = tick.kind
    # The analysis found something important - the human learns about it in telegram,
    # not whenever they open the "Advice" screen themselves (task D5).
    message = notifier.digest_if_worth(result, result.get("advice") or [])
    if message is not None:
        notifier.dispatch(conn, [message], config)
    log.info(
        "tick %s: tips %s, cost $%.4f",
        tick.kind,
        len(result["advice"]),
        result["cost_usd"],
    )
    return result


async def loop(
    open_db: Any,
    load_config: Any,
    *,
    runner: Any = None,
    interval_seconds: float = 60.0,
) -> None:
    """Background loop: once a minute it asks the scheduler whether it is time.

    Polling is cheap (a single SELECT), and the decision is made by `plan_tick` - that way
    a tick does not drift because the server was restarted.
    """
    started_at = datetime.now(UTC)

    def tick_now() -> None:
        # The connection is opened right here: an SQLite object belongs to the thread
        # it was created in, and the whole tick runs in a separate thread.
        config = load_config()
        conn = open_db()
        try:
            now = datetime.now(UTC)
            tick = plan_tick(conn, now, config, started_at=started_at)
            if tick is not None:
                run_tick(conn, config, tick, runner=runner)
            # Notifications live on the same tick: the loop already ticks once a
            # minute, and starting a second one for two checks is pointless (D5).
            messages = notifier.alerts_for(conn, metrics.overview(conn, now), config, now=now)
            daily = notifier.daily_if_due(conn, config, now=now)
            if daily is not None:
                messages.append(daily)
            if messages:
                notifier.dispatch(conn, messages, config, now=now)
        finally:
            conn.close()

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(tick_now)
        except asyncio.CancelledError:
            raise
        except Exception:  # a tick must not bring the server down
            log.exception("the advisor tick failed")


def _last_tick(conn: sqlite3.Connection, kind: str | None = None) -> datetime | None:
    """The time of the last tick: of the given kind or of any kind."""
    if kind is None:
        row = conn.execute("SELECT MAX(ts) AS ts FROM advice").fetchone()
    else:
        row = conn.execute("SELECT MAX(ts) AS ts FROM advice WHERE kind = ?", (kind,)).fetchone()
    stamp = row["ts"] if row else None
    if not stamp:
        return None
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(UTC)


def _has_turns(conn: sqlite3.Connection, since: datetime, until: datetime) -> bool:
    row = conn.execute(
        "SELECT 1 FROM turns WHERE ts >= ? AND ts < ? LIMIT 1",
        (metrics._utc_stamp(since), metrics._utc_stamp(until)),
    ).fetchone()
    return row is not None
