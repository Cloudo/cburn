"""Когда стоит написать в telegram, а когда промолчать (задача D5, ТЗ §7).

Решения вынесены в чистые функции: сеть и база остаются снаружи, а правила
проверяются тестами без единого запроса. Правил три и все они про одно —
не будить человека без повода:

* часовая выжимка уходит, только если советчик нашёл что-то важнее `info`;
* дневная сводка — раз в сутки в назначенное время, и только один раз;
* мгновенный алерт по сессии — не чаще, чем раз в `COOLDOWN`.

Поверх всего стоит глобальная пауза: два часа тишины, которые не отменяют
только `crit` — если расход горит прямо сейчас, молчать нельзя.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: Насколько замолкает конкретная сессия после своего алерта. Полчаса — это
#: примерно длина одного захода работы: чаще напоминать не о чем.
COOLDOWN = timedelta(minutes=30)

#: Сколько длится глобальная пауза по кнопке (ТЗ §5).
PAUSE = timedelta(hours=2)

#: Порядок важности: сравнение идёт по этому списку, а не по алфавиту.
SEVERITY = ("info", "warn", "crit")


def rank(severity: str | None) -> int:
    """Место важности в шкале; незнакомое слово считается самым мягким."""
    try:
        return SEVERITY.index(severity or "info")
    except ValueError:
        return 0


@dataclass(frozen=True)
class Message:
    """Готовое сообщение: текст уже собран, каналу остаётся его отправить."""

    kind: str  # digest | daily | alert
    text: str
    severity: str = "info"
    #: Чем помечена память об этом сообщении: сессия у алерта, дата у сводки.
    key: str | None = None


def is_paused(paused_until: datetime | None, now: datetime) -> bool:
    return paused_until is not None and paused_until > now


def allowed(message: Message, paused_until: datetime | None, now: datetime) -> bool:
    """Пропускать ли сообщение сквозь паузу.

    `crit` проходит всегда: пауза — это «не отвлекай по мелочам», а не
    «выключи прибор».
    """
    return message.severity == "crit" or not is_paused(paused_until, now)


def digest_message(severity: str | None, summary: str) -> Message | None:
    """Часовая выжимка советчика — только если есть о чём говорить."""
    if rank(severity) < rank("warn"):
        return None
    return Message(kind="digest", text=summary, severity=severity or "warn")


def daily_message(
    now: datetime,
    at: str,
    last_daily: datetime | None,
    summary: str,
) -> Message | None:
    """Дневная сводка: раз в сутки, после назначенного времени.

    Время сравнивается по местным часам — «21:00» человек читает как вечер у
    себя, а не в UTC. Если дашборд был выключен, сводка уйдёт при первом
    запуске после срока, но всё равно один раз за день.
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
    """Мгновенные алерты с cooldown по сессии.

    `candidates` — тройки «ключ, важность, текст»: правила, что считать
    поводом, живут в вызывающем коде, здесь только память о том, кого мы
    недавно уже трогали.
    """
    fresh: list[Message] = []
    for key, severity, text in candidates:
        last = last_alerts.get(key)
        if last is not None and now - last < COOLDOWN:
            continue
        fresh.append(Message(kind="alert", text=text, severity=severity, key=key))
    return fresh


def pause_until(now: datetime | None = None) -> datetime:
    """До какого момента молчать после нажатия «пауза на 2 часа»."""
    return (now or datetime.now(UTC)) + PAUSE
