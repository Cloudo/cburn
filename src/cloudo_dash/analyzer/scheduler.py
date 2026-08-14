"""Планировщик советчика (задача D3, ТЗ §6).

Такт раз в `analyzer.interval_minutes`, раз в неделю — глубокий разбор на
модели побольше. Каждый такт стоит денег, поэтому решение «звать модель или
нет» вынесено в чистую функцию `plan_tick`: её видно в тестах, и она молчит,
когда советовать не о чем.

Пропускаем такт, когда:

* советчик выключен в конфиге;
* с прошлого такта не прошёл интервал;
* за период не было ни одного хода — разбирать нечего.
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
#: Разбор, запущенный человеком: из CLI или кнопкой на экране «Советы».
#: На расписание влияет так же, как часовой — интервал считается от любого
#: такта, — но в истории должен быть подписан честно.
MANUAL = "manual"

#: Неделя между глубокими разборами. Считается от прошлого недельного такта,
#: а не по календарю: перезапуск сервера не должен сдвигать расписание.
WEEKLY_PERIOD = timedelta(days=7)

#: Сколько ждать после старта до первого такта. Сервер часто перезапускают, и
#: такт на каждом старте — это деньги на ровном месте.
WARMUP = timedelta(minutes=5)


@dataclass(frozen=True)
class Tick:
    """Что и за какой период разбирать."""

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
    """Решить, нужен ли такт прямо сейчас. `None` — пропускаем."""
    analyzer = config.get("analyzer") or {}
    if not analyzer.get("enabled", True):
        return None
    if started_at is not None and now - started_at < WARMUP:
        return None

    interval = timedelta(minutes=float(analyzer.get("interval_minutes") or 60))
    # Интервал считается от любого такта, а не только от часового: недельный
    # разбор только что посмотрел те же данные, повторять их за $0.08 незачем.
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
        log.debug("такт пропущен: за период не было ходов")
        return None
    return Tick(HOURLY, since, str(analyzer.get("model") or "haiku"))


def run_tick(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    tick: Tick,
    *,
    runner: Any = None,
) -> dict[str, Any]:
    """Собрать дайджест за период такта и прогнать его через советчика."""
    payload = digest.build(conn, tick.since, config=config)
    result = advisor.advise(conn, payload, model=tick.model, runner=runner, kind=tick.kind)
    result["kind"] = tick.kind
    # Разбор нашёл что-то важное — человек узнает об этом в телеграме, а не
    # когда сам откроет экран «Советы» (задача D5).
    message = notifier.digest_if_worth(result, result.get("advice") or [])
    if message is not None:
        notifier.dispatch(conn, [message], config)
    log.info(
        "такт %s: советов %s, стоил $%.4f",
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
    """Фоновый цикл: раз в минуту спрашивает планировщик, не пора ли.

    Опрос дешёвый (один SELECT), а решение принимает `plan_tick` — так такт не
    съезжает от того, что сервер перезапускали.
    """
    started_at = datetime.now(UTC)

    def tick_now() -> None:
        # Соединение открывается здесь же: объект SQLite принадлежит потоку,
        # в котором создан, а весь такт уходит в отдельный поток.
        config = load_config()
        conn = open_db()
        try:
            now = datetime.now(UTC)
            tick = plan_tick(conn, now, config, started_at=started_at)
            if tick is not None:
                run_tick(conn, config, tick, runner=runner)
            # Уведомления живут на том же такте: цикл и так тикает раз в
            # минуту, а заводить второй ради двух проверок незачем (D5).
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
        except Exception:  # такт не должен ронять сервер
            log.exception("такт советчика не отработал")


def _last_tick(conn: sqlite3.Connection, kind: str | None = None) -> datetime | None:
    """Время последнего такта: указанного вида или любого."""
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
