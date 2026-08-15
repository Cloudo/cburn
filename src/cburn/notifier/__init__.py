"""Уведомления в telegram: бридж на localhost:8788 или прямой Bot API (TZ §7, M3).

Здесь всё, что связывает правила (`rules`) с базой и каналом (`channel`):
что уже отправлено, когда была последняя тревога по сессии и стоит ли сейчас
пауза. Тексты собираются тут же — коротко, цифрами, без пересказа переписки.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .. import metrics
from .channel import Channel
from .rules import Message, alert_messages, daily_message, digest_message, pause_until, rank

log = logging.getLogger(__name__)

#: Ключ паузы в `notifier_state`.
PAUSE_KEY = "paused_until"

__all__ = [
    "Channel",
    "Message",
    "alerts_for",
    "daily_if_due",
    "daily_text",
    "digest_if_worth",
    "digest_text",
    "dispatch",
    "last_alerts",
    "pause_until",
    "paused_until",
    "set_pause",
]


def paused_until(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute("SELECT value FROM notifier_state WHERE key = ?", (PAUSE_KEY,)).fetchone()
    if row is None or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


def set_pause(conn: sqlite3.Connection, until: datetime | None) -> None:
    """Поставить или снять паузу. `None` — снять."""
    with conn:
        conn.execute(
            "INSERT INTO notifier_state (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (PAUSE_KEY, until.isoformat() if until else ""),
        )


def last_alerts(conn: sqlite3.Connection) -> dict[str, datetime]:
    """Когда в последний раз тревожили по каждой сессии."""
    rows = conn.execute(
        "SELECT key, MAX(ts) AS ts FROM notifications"
        " WHERE kind = 'alert' AND key IS NOT NULL GROUP BY key"
    )
    result: dict[str, datetime] = {}
    for row in rows:
        try:
            result[row["key"]] = datetime.fromisoformat(row["ts"])
        except (TypeError, ValueError):
            continue
    return result


def last_sent(conn: sqlite3.Connection, kind: str) -> datetime | None:
    row = conn.execute(
        "SELECT MAX(ts) FROM notifications WHERE kind = ? AND ok = 1", (kind,)
    ).fetchone()
    if row is None or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


def remember(conn: sqlite3.Connection, message: Message, channel: str, ok: bool) -> None:
    with conn:
        conn.execute(
            "INSERT INTO notifications (ts, kind, key, severity, channel, text, ok)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                message.kind,
                message.key,
                message.severity,
                channel,
                message.text[:2000],
                int(ok),
            ),
        )


def digest_text(run: dict[str, Any], items: list[dict[str, Any]]) -> str:
    """Часовая выжимка: во что обошёлся период и что советчик предлагает."""
    lines = [
        f"Разбор за час: {len(items)} совет(а/ов), такт ${run.get('cost_usd', 0):.2f}",
    ]
    for item in items[:3]:
        mark = {"crit": "🔴", "warn": "🟠"}.get(item.get("severity", "info"), "•")
        lines.append(f"{mark} {item.get('title', '')}".strip())
    lines.append("Подробности — на экране «Советы».")
    return "\n".join(lines)


def daily_text(usage: dict[str, Any], top: list[dict[str, Any]]) -> str:
    """Дневная сводка: сколько сожгли и кто больше всех."""
    tokens = usage.get("tokens", 0)
    lines = [
        f"За день: {usage.get('turns', 0)} ходов, {tokens / 1000:.0f}k токенов,"
        f" ${usage.get('cost_usd', 0):.2f} по тарифам API",
    ]
    for session in top[:3]:
        name = session.get("title") or (session.get("id") or "")[:8]
        lines.append(f"• {name} — {session.get('tokens', 0) / 1000:.0f}k")
    return "\n".join(lines)


def alerts_for(
    conn: sqlite3.Connection,
    overview: dict[str, Any],
    config: dict[str, Any],
    now: datetime | None = None,
) -> list[Message]:
    """Что горит прямо сейчас: расход выше порога и раздутый контекст.

    Оба повода уже посчитаны для дашборда — тревога не заводит своей арифметики,
    иначе на экране и в телефоне были бы разные цифры.
    """
    moment = now or datetime.now(UTC)
    thresholds = config.get("thresholds") or {}
    burn_limit = float(thresholds.get("burn_rate_warn_per_min") or 0)
    context_crit = float(thresholds.get("context_crit") or 0)

    candidates: list[tuple[str, str, str]] = []
    burn = (overview.get("burn") or {}).get("1m") or {}
    tokens_per_min = float(burn.get("tokens_per_min") or 0)
    if burn_limit and tokens_per_min >= burn_limit:
        candidates.append(
            (
                "burn",
                "crit",
                f"Расход {tokens_per_min / 1000:.0f}k ток/мин —"
                f" выше порога {burn_limit / 1000:.0f}k",
            )
        )
    for session in overview.get("live_sessions") or []:
        context = float(session.get("last_context") or 0)
        if context_crit and context >= context_crit:
            name = session.get("title") or (session.get("id") or "")[:8]
            candidates.append(
                (
                    str(session.get("id")),
                    "warn",
                    f"{name}: контекст {context / 1000:.0f}k — пора /clear",
                )
            )
    return alert_messages(moment, candidates, last_alerts(conn))


def dispatch(
    conn: sqlite3.Connection,
    messages: list[Message],
    config: dict[str, Any],
    now: datetime | None = None,
) -> int:
    """Отправить то, что прошло правила; вернуть число ушедших сообщений."""
    moment = now or datetime.now(UTC)
    channel = Channel(config)
    if not channel.enabled:
        return 0
    quiet_until = paused_until(conn)
    sent = 0
    for message in messages:
        if message.severity != "crit" and quiet_until is not None and quiet_until > moment:
            log.info("уведомление придержано паузой: %s", message.kind)
            continue
        error = channel.send(message.text, message.severity)
        remember(conn, message, channel.mode, ok=error is None)
        if error is None:
            sent += 1
    return sent


def daily_if_due(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    now: datetime | None = None,
) -> Message | None:
    """Дневная сводка, если её время пришло и сегодня она ещё не уходила."""
    moment = now or datetime.now(UTC)
    telegram = config.get("telegram") or {}
    at = str(telegram.get("daily_summary_at") or "21:00")
    day_start = metrics.local_day_start(moment)
    usage = metrics.window_usage(conn, day_start)
    top = metrics.top_sessions(conn, day_start)
    return daily_message(moment, at, last_sent(conn, "daily"), daily_text(usage, top))


def digest_if_worth(run: dict[str, Any], items: list[dict[str, Any]]) -> Message | None:
    """Часовая выжимка, если советчик нашёл хоть что-то важнее `info`."""
    severity = run.get("max_severity")
    if rank(severity) < rank("warn"):
        return None
    return digest_message(severity, digest_text(run, items))
