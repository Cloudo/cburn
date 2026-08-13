"""Сверка E3: метрики OTel против ходов из транскрипта на одной сессии.

Транскрипт сессии дочитывается в ту же БД, куда лёг приём телеметрии, после
чего обе стороны считаются по своим таблицам и сравниваются по токенам и
стоимости. Порог приёмки — расхождение не больше 2% (ТЗ §10, M4).

Сравнивается основной запрос (`query_source = main`): служебные запросы
Claude Code (генерация заголовка сессии и подобное) в транскрипт не попадают
вовсе, поэтому их видно только со стороны телеметрии и они печатаются отдельно.

    python tools/e3_compare.py <база> <каталог проекта в ~/.claude/projects> <session_id>
"""

import sys
from pathlib import Path

from cloudo_dash import config, pricing
from cloudo_dash.collector.indexer import ingest_tree
from cloudo_dash.db import connect

db_path = Path(sys.argv[1])
project_dir = Path(sys.argv[2])
session_id = sys.argv[3]

conn = connect(db_path)
pricing.sync_prices(conn, config.load())
list(ingest_tree(conn, project_dir.parent))

turns = conn.execute(
    "SELECT COUNT(*) AS turns, SUM(input_tokens) AS input, SUM(output_tokens) AS output,"
    "       SUM(cache_read) AS cache_read, SUM(cache_write_5m + cache_write_1h) AS cache_write,"
    "       SUM(cost_usd) AS cost"
    " FROM turns WHERE session_id = ?",
    (session_id,),
).fetchone()


def metric(name: str, kind: str | None, source: str) -> float:
    row = conn.execute(
        "SELECT SUM(value) FROM otel_metrics"
        " WHERE name = ? AND session_id = ? AND json_extract(attrs, '$.query_source') = ?"
        "   AND (? IS NULL OR kind = ?)",
        (name, session_id, source, kind, kind),
    ).fetchone()
    return float(row[0] or 0)


TOKENS = "claude_code.token.usage"
COST = "claude_code.cost.usage"

print(f"сессия {session_id}: ходов в транскрипте {turns['turns']}")
print(f"{'величина':12} {'JSONL':>14} {'OTel main':>14} {'расхождение':>12}")
worst = 0.0
for label, left, right in (
    ("input", turns["input"], metric(TOKENS, "input", "main")),
    ("output", turns["output"], metric(TOKENS, "output", "main")),
    ("cache_read", turns["cache_read"], metric(TOKENS, "cacheRead", "main")),
    ("cache_write", turns["cache_write"], metric(TOKENS, "cacheCreation", "main")),
    ("cost_usd", turns["cost"], metric(COST, None, "main")),
):
    left = float(left or 0)
    base = max(abs(left), abs(right))
    delta = 0.0 if base == 0 else abs(left - right) / base * 100
    worst = max(worst, delta)
    print(f"{label:12} {left:14,.4f} {right:14,.4f} {delta:11.2f}%")

aux_tokens = sum(
    metric(TOKENS, kind, "auxiliary") for kind in ("input", "output", "cacheRead", "cacheCreation")
)
print(
    f"служебные запросы мимо транскрипта: {aux_tokens:,.0f} токенов,"
    f" ${metric(COST, None, 'auxiliary'):.6f}"
)

events = conn.execute(
    "SELECT name, COUNT(*) AS n FROM otel_events WHERE session_id = ?"
    " GROUP BY name ORDER BY n DESC",
    (session_id,),
).fetchall()
print("события:", ", ".join(f"{row['name']}×{row['n']}" for row in events))
print(f"худшее расхождение: {worst:.2f}% (порог 2%)")
conn.close()
