"""Check E3: OTel metrics against transcript turns on one session.

The session transcript is read into the same database the telemetry landed in, after
which both sides are counted over their own tables and compared by tokens and
cost. The acceptance threshold is a mismatch of no more than 2% (SPEC §10, M4).

The main request is compared (`query_source = main`): Claude Code service requests
(session title generation and the like) never reach the transcript at all, so they are
visible only on the telemetry side and are printed separately.

    python tools/e3_compare.py <database> <project dir in ~/.claude/projects> <session_id>
"""

import sys
from pathlib import Path

from cburn import config, pricing
from cburn.collector.indexer import ingest_tree
from cburn.db import connect

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

print(f"session {session_id}: turns in the transcript {turns['turns']}")
print(f"{'value':12} {'JSONL':>14} {'OTel main':>14} {'mismatch':>12}")
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
    f"service requests past the transcript: {aux_tokens:,.0f} tokens,"
    f" ${metric(COST, None, 'auxiliary'):.6f}"
)

events = conn.execute(
    "SELECT name, COUNT(*) AS n FROM otel_events WHERE session_id = ?"
    " GROUP BY name ORDER BY n DESC",
    (session_id,),
).fetchall()
print("events:", ", ".join(f"{row['name']}x{row['n']}" for row in events))
print(f"worst mismatch: {worst:.2f}% (threshold 2%)")
conn.close()
