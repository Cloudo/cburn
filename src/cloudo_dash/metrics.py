"""Запросы к БД. Тяжёлые агрегаты считаются в SQL, не в Python.

Пока здесь только то, что нужно для сверки цифр по сессии (задача A3);
метрики ТЗ §4 — burn rate по окнам, доля моделей, холостые ходы — задача B3.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class SessionSummary:
    """Итоги по одной сессии."""

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
    """Суммы по сессии. Считаются из `turns`, а не из кэша в `sessions`."""
    row = conn.execute(
        """
        SELECT s.id                                        AS session_id,
               p.slug                                      AS project,
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
               -- Расход сабагентов входит в сессию и виден отдельной строкой (ТЗ §4).
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
    """Распределение ходов и выходных токенов по моделям."""
    return [
        (row["model"] or "—", row["turns"], row["output_tokens"])
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
    """Профиль инструментов сессии: для Bash детализация — задача B3."""
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


def recent_sessions(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    """Последние сессии по активности (фильтры по проекту и периоду — задача B7)."""
    return list(
        conn.execute(
            """
            SELECT s.id, p.slug AS project, s.last_at, s.started_at, s.turns, s.tokens_out,
                   s.cache_read, s.cache_write, s.last_context, s.first_prompt,
                   s.title, s.title_source
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE s.hidden = 0
             ORDER BY s.last_at DESC LIMIT ?
            """,
            (limit,),
        )
    )


# --- обзор (задача A5) -------------------------------------------------------

#: Окна burn rate в секундах. Десятисекундное — «что происходит прямо сейчас»:
#: ход добавляет в окно сотни тысяч токенов разом, и в минутном усреднении это
#: видно как ступенька длиной в минуту (ТЗ §4).
BURN_WINDOWS = (10, 60, 300, 3600)

#: Формат времени в транскриптах: UTC с Z. Строковое сравнение здесь корректно
#: и позволяет фильтровать по времени прямо в SQL, без разбора дат.
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TS_FORMAT)


def window_usage(conn: sqlite3.Connection, since: datetime, until: datetime | None = None) -> dict:
    """Суммы по ходам в интервале времени."""
    params: list[str] = [_utc_stamp(since)]
    clause = "ts >= ?"
    if until is not None:
        clause += " AND ts < ?"
        params.append(_utc_stamp(until))
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
        """,  # noqa: S608 — параметры подставляются через плейсхолдеры
        params,
    ).fetchone()
    usage = dict(row)
    usage["cache_write"] = usage["cache_write_5m"] + usage["cache_write_1h"]
    # Полный объём токенов, прошедших через модель: им и живёт стрелка спидометра.
    usage["tokens"] = (
        usage["input_tokens"] + usage["output_tokens"] + usage["cache_read"] + usage["cache_write"]
    )
    return usage


def window_key(seconds: int) -> str:
    """Ключ окна в ответе API: 10s, 1m, 5m, 60m."""
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m"


def burn_rates(conn: sqlite3.Connection, now: datetime) -> dict[str, dict]:
    """Burn rate по окнам ТЗ §4 — всегда в токенах в минуту, окна разной длины."""
    rates: dict[str, dict] = {}
    for seconds in BURN_WINDOWS:
        usage = window_usage(conn, now - timedelta(seconds=seconds))
        minutes = seconds / 60
        rates[window_key(seconds)] = {
            "tokens_per_min": usage["tokens"] / minutes,
            "output_per_min": usage["output_tokens"] / minutes,
            "cost_per_hour": usage["cost_usd"] / minutes * 60,  # цены — задача B1
            "turns": usage["turns"],
            "sessions": usage["sessions"],
            "window_seconds": seconds,
            # Абсолютные суммы за окно: из них считается разбивка по
            # составляющим, и она должна уметь показывать любое окно.
            "usage": usage,
        }
    return rates


#: Сколько живых сессий показывать на дашборде.
LIVE_LIMIT = 5

#: Окно, в котором сессия ещё считается недавней и попадает на дашборд.
LIVE_WINDOW_SECONDS = 3600

#: После этой паузы сессия перестаёт быть «сейчас» и уходит в простой.
IDLE_AFTER_SECONDS = 120

#: Столько ждём ответа инструмента, прежде чем решить, что висит запрос
#: разрешения: обычный инструмент отвечает быстрее.
PERMISSION_AFTER_SECONDS = 25

#: Статусы сессии — по тому, кого она в этот момент ждёт.
STATUS_WORKING = "working"  # ход не завершён: модель думает или гоняет инструменты
STATUS_PERMISSION = "permission"  # инструмент запрошен, ответа нет — висит разрешение
STATUS_ANSWERED = "answered"  # модель ответила и ждёт человека
STATUS_IDLE = "idle"  # тишина дольше IDLE_AFTER_SECONDS, но процесс жив
STATUS_DONE = "done"  # процесса нет: в эту сессию больше не пишут


def session_status(row: dict, now: datetime) -> str:
    """Кого сессия ждёт прямо сейчас.

    Ход не завершён — работает модель: и когда она думает над промптом, и когда
    крутит инструменты. Завершённый ход означает обратное: ждут человека.
    Отдельный случай — инструмент запрошен, а результата нет: почти всегда это
    висящий запрос разрешения.

    Тишина сама по себе не отличает паузу от конца работы: транскрипт не знает,
    что сессия закрылась. Это знает `is_live` — флаг ставится по списку
    процессов Claude Code (задача B4). Отсутствие процесса засчитывается только
    после `IDLE_AFTER_SECONDS`: флаг обновляется не мгновенно, и живая сессия
    не должна мигать «закончилась» между опросами.
    """
    quiet = _seconds_since(row.get("last_record_at") or row.get("last_at"), now)
    kind = row.get("last_record_kind")

    if kind == "assistant" and row.get("last_stop_reason") == "tool_use":
        if quiet >= PERMISSION_AFTER_SECONDS:
            return STATUS_PERMISSION
        return STATUS_WORKING
    if quiet >= IDLE_AFTER_SECONDS:
        return STATUS_DONE if row.get("is_live") == 0 else STATUS_IDLE
    if kind in {"prompt", "tool_result"}:
        return STATUS_WORKING
    return STATUS_ANSWERED


def _seconds_since(stamp: str | None, now: datetime) -> float:
    if not stamp:
        return float("inf")
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (now - moment).total_seconds()


def live_sessions(
    conn: sqlite3.Connection,
    now: datetime,
    seconds: int = LIVE_WINDOW_SECONDS,
    limit: int = 40,
) -> list[dict]:
    """Недавние сессии с их статусами (уточнение живости — задача B4).

    Скрытые вручную не показываются, порядок — по последней активности.
    """
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, p.slug AS project, p.root_path, s.last_at, s.started_at,
                   s.turns, s.tokens_out, s.last_context, s.first_prompt, s.last_prompt,
                   s.title, s.title_source, s.last_record_kind, s.last_record_at,
                   s.last_stop_reason, s.is_live,
                   (SELECT COALESCE(SUM(t.output_tokens), 0) FROM turns AS t
                     WHERE t.session_id = s.id AND t.ts >= ?) AS output_recent
              FROM sessions AS s
              LEFT JOIN projects AS p ON p.id = s.project_id
             WHERE s.last_at >= ? AND s.hidden = 0
             ORDER BY s.last_at DESC
             LIMIT ?
            """,
            (_utc_stamp(now - timedelta(seconds=IDLE_AFTER_SECONDS)),)
            + (_utc_stamp(now - timedelta(seconds=seconds)), limit),
        )
    ]
    for row in rows:
        row["status"] = session_status(row, now)
    return rows


def session_chain(conn: sqlite3.Connection, session_id: str) -> dict:
    """Вся линия работы, к которой принадлежит сессия (задача B5).

    Resume копирует историю в новый `sessionId`, поэтому одна работа
    рассыпана по нескольким сессиям. Линия строится от корня цепочки вниз;
    ходы при этом не задваиваются — копии гасятся дедупликацией по
    `message_id` ещё при импорте.
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


def refresh_liveness(conn: sqlite3.Connection, active_ids: set[str] | None) -> int:
    """Проставить `is_live` по списку живых сессий Claude Code (задача B4).

    `active_ids=None` означает «спросить не удалось» — тогда флаги остаются
    как были: лучше устаревшая живость, чем ложное «закончилась» на всех.
    Возвращает число изменённых строк.
    """
    if active_ids is None:
        return 0
    ids = tuple(active_ids)
    live = f"id IN ({','.join('?' * len(ids))})" if ids else "0"
    with conn:
        cursor = conn.execute(
            f"UPDATE sessions SET is_live = ({live}) WHERE is_live IS NOT ({live})",  # noqa: S608
            ids * 2,
        )
    return cursor.rowcount


def top_sessions(conn: sqlite3.Connection, since: datetime, limit: int = 5) -> list[dict]:
    """Самые расходные сессии периода."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, p.slug AS project, s.last_at, s.first_prompt, s.title, s.last_context,
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
    """Начало сегодняшнего дня по местному времени: «за сегодня» — про человека."""
    local = now.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def overview(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """Сводка для главного экрана (ТЗ §5, «Обзор»)."""
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
        "series_bucket_seconds": SERIES_BUCKET_SECONDS,
        "totals": dict(totals),
    }


def data_stamps(conn: sqlite3.Connection, now: datetime) -> dict[str, str | None]:
    """Время самого свежего события в каждом срезе обзора.

    Виджет показывает не момент пересчёта (он идёт раз в секунду и без новых
    ходов), а время данных, на которых он стоит: в паузе метка честно замирает.
    Срезы разные, поэтому и времена разные: у ленты — последний ход вообще,
    у дневных виджетов — последний ход с местной полуночи.
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
    """Лента последних ходов с инструментами, которые в них вызывались."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.message_id, t.session_id, t.ts, t.model, t.output_tokens,
                   t.input_tokens, t.cache_read, t.is_sidechain,
                   t.cache_write_5m + t.cache_write_1h AS cache_write,
                   t.context_estimate, p.slug AS project,
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


#: Шаг самописца. Мельче нет смысла: Claude Code дописывает транскрипт
#: порциями раз в 2–6 секунд, а расход хода известен только по его завершении.
SERIES_BUCKET_SECONDS = 5
SERIES_SPAN_MINUTES = 5


def burn_series(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    bucket_seconds: int = SERIES_BUCKET_SECONDS,
    span_minutes: int = SERIES_SPAN_MINUTES,
) -> list[dict]:
    """Расход по корзинам времени — лента самописца за последние минуты.

    Пустые корзины заполняются нулями: без них провал в работе выглядел бы
    как непрерывная нагрузка, только с редкими точками.
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
    """Сессии, в которых модель работает прямо сейчас (ТЗ §4).

    Токены такого запроса ещё неизвестны: они появятся в транскрипте только
    вместе с завершённым ходом.
    """
    return [
        session["id"] for session in live_sessions(conn, now) if session["status"] == STATUS_WORKING
    ]


def set_hidden(conn: sqlite3.Connection, session_id: str, hidden: bool) -> bool:
    """Убрать сессию с дашборда или вернуть обратно. В транскриптах ничего не меняется."""
    with conn:
        cursor = conn.execute(
            "UPDATE sessions SET hidden = ? WHERE id = ?", (int(hidden), session_id)
        )
    return cursor.rowcount > 0


# --- метрики ТЗ §4 (задача B3) -----------------------------------------------

#: Холостой ход: модель ответила почти ничего, хотя контекст уже большой —
#: случай «жду» из отчёта (ТЗ §4). Пороги вынесены сюда, а не в конфиг:
#: это определение метрики, а не настройка.
IDLE_MAX_OUTPUT = 10
IDLE_MIN_CONTEXT = 50_000

#: Окно лимитов подписки: 5 часов с первого хода серии. Точку отсчёта Claude
#: Code в транскрипт не пишет, поэтому окно восстанавливается по данным и
#: помечается приближением (уточнение по OTel — веха E).
LIMIT_WINDOW_HOURS = 5
WEEK_HOURS = 24 * 7


def model_share(conn: sqlite3.Connection, since: datetime) -> list[dict]:
    """Доля моделей за период: ходы и токены (ТЗ §4)."""
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT COALESCE(model, '—')                      AS model,
                   COUNT(*)                                  AS turns,
                   COALESCE(SUM(output_tokens), 0)           AS output_tokens,
                   COALESCE(SUM(input_tokens + output_tokens + cache_read
                                + cache_write_5m + cache_write_1h), 0) AS tokens
              FROM turns WHERE ts >= ?
             GROUP BY model ORDER BY tokens DESC
            """,
            (_utc_stamp(since),),
        )
    ]


def tool_profile(conn: sqlite3.Connection, since: datetime, limit: int = 8) -> dict:
    """Профиль инструментов за период; внутри Bash — по нормализованным командам."""
    tools = [
        dict(row)
        for row in conn.execute(
            """
            SELECT c.tool, COUNT(*) AS calls
              FROM tool_calls AS c JOIN turns AS t ON t.id = c.turn_id
             WHERE t.ts >= ?
             GROUP BY c.tool ORDER BY calls DESC
            """,
            (_utc_stamp(since),),
        )
    ]
    commands = [
        dict(row)
        for row in conn.execute(
            """
            SELECT c.detail AS command, COUNT(*) AS calls
              FROM tool_calls AS c JOIN turns AS t ON t.id = c.turn_id
             WHERE t.ts >= ? AND c.tool = 'Bash' AND c.detail IS NOT NULL
             GROUP BY c.detail ORDER BY calls DESC LIMIT ?
            """,
            (_utc_stamp(since), limit),
        )
    ]
    return {
        "tools": tools[:limit],
        "tools_total": sum(row["calls"] for row in tools),
        "bash_commands": commands,
    }


def idle_turns(conn: sqlite3.Connection, since: datetime) -> dict:
    """Холостые ходы за период: сколько их и во что обошлись."""
    row = conn.execute(
        """
        SELECT COUNT(*)                        AS turns,
               COALESCE(SUM(cache_read), 0)    AS cache_read,
               COALESCE(SUM(output_tokens), 0) AS output_tokens
          FROM turns
         WHERE ts >= ? AND output_tokens < ? AND context_estimate > ?
        """,
        (_utc_stamp(since), IDLE_MAX_OUTPUT, IDLE_MIN_CONTEXT),
    ).fetchone()
    total = conn.execute(
        "SELECT COUNT(*) FROM turns WHERE ts >= ?", (_utc_stamp(since),)
    ).fetchone()[0]
    idle = dict(row)
    idle["share"] = idle["turns"] / total if total else 0.0
    idle["max_output"] = IDLE_MAX_OUTPUT
    idle["min_context"] = IDLE_MIN_CONTEXT
    return idle


def limit_window(conn: sqlite3.Connection, now: datetime) -> dict:
    """Оценка окна лимитов подписки — приближение (ТЗ §4).

    Claude Code не пишет в транскрипт ни границ окна, ни самих лимитов, так
    что окно восстанавливается по ходам: оно начинается с первого хода после
    паузы длиннее пяти часов и столько же длится. Считаем расход внутри
    текущего окна и за скользящую неделю; «сколько осталось» без лимитов
    сказать нельзя, поэтому отдаём объём, а не проценты.
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
    """Начало текущего пятичасового окна: первый ход после паузы длиннее окна."""
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
        if moment - start >= span:  # прошлое окно закрылось, началось новое
            start = moment
    return start


def _parse_stamp(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
