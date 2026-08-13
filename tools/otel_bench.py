"""Замер: во что обходятся подзапросы телеметрии в списках сессий (веха E).

Обзор пересчитывается раз в секунду, а к каждой сессии в нём цепляются два
подзапроса по `otel_events`. На реалистичном объёме (сотни сессий, сотни тысяч
событий) это должно оставаться дешёвым — иначе телеметрия платит собой за
собственную полезность.

    python tools/otel_bench.py [сессий] [событий на сессию]
"""

import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cloudo_dash import metrics
from cloudo_dash.collector import otlp
from cloudo_dash.db import connect

sessions = int(sys.argv[1]) if len(sys.argv) > 1 else 200
per_session = int(sys.argv[2]) if len(sys.argv) > 2 else 500

db_path = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/tmp/otel-bench.db")
db_path.unlink(missing_ok=True)
conn = connect(db_path)

now = datetime.now(UTC)
names = ("api_request", "tool_result", "tool_decision", "hook_execution_start")

with conn:
    for index in range(sessions):
        started = now - timedelta(minutes=index)
        conn.execute(
            "INSERT INTO sessions (id, last_at, last_record_kind, last_record_at,"
            " last_stop_reason, is_live) VALUES (?, ?, 'assistant', ?, 'tool_use', 1)",
            (f"s{index}", metrics._utc_stamp(started), metrics._utc_stamp(started)),
        )

events = []
for index in range(sessions):
    for number in range(per_session):
        # События раскиданы по сроку хранения: за сутки их доля и попадает
        # в обзор, а весь объём лежит в тех же таблицах.
        moment = now - timedelta(minutes=index, seconds=number * 30)
        events.append(
            otlp.EventRecord(
                name=names[number % len(names)],
                ts=otlp.stamp(moment),
                session_id=f"s{index}",
                attrs={"tool_name": "Bash", "duration_ms": "968", "event.sequence": number},
            )
        )
started = time.monotonic()
otlp.store_events(conn, events)
print(f"записано {len(events):,} событий за {time.monotonic() - started:.1f} с")


def timed(call, runs: int = 3) -> float:
    """Лучшее из нескольких прогонов: интересен потолок, а не разброс."""
    best = float("inf")
    for _ in range(runs):
        started = time.monotonic()
        call()
        best = min(best, time.monotonic() - started)
    return best


day = now - timedelta(days=1)
for label, call in (
    ("live_sessions", lambda: metrics.live_sessions(conn, now)),
    ("sessions_page", lambda: metrics.sessions_page(conn, now=now, limit=100)),
    ("overview", lambda: metrics.overview(conn, now)),
    # Так обзор считается между обновлениями среза: сервер держит его в кэше
    # на несколько секунд (`OTEL_CACHE_SECONDS`).
    ("overview (кэш)", lambda: metrics.overview(conn, now, otel={})),
    ("otel_state", lambda: metrics.otel_state(conn, day)),
    ("  otel_usage", lambda: metrics.otel_usage(conn, day)),
    ("  otel_permissions", lambda: metrics.otel_permissions(conn, day)),
    ("  otel_errors", lambda: metrics.otel_errors(conn, day)),
    ("  otel_work", lambda: metrics.otel_work(conn, day)),
):
    print(f"{label:18}: {timed(call) * 1000:7.1f} мс")

conn.close()
