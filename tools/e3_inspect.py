"""Inspecting telemetry data: what exactly arrived and in what shape (milestone E)."""

import json
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
conn.row_factory = sqlite3.Row

print("== token.usage by query_source ==")
for row in conn.execute(
    "SELECT kind, json_extract(attrs,'$.query_source') AS src, model, SUM(value) AS v"
    " FROM otel_metrics WHERE name='claude_code.token.usage' GROUP BY kind, src, model"
):
    print(dict(row))

print("== events ==")
for row in conn.execute("SELECT name, ts, attrs FROM otel_events ORDER BY ts"):
    attrs = json.loads(row["attrs"])
    attrs.pop("event.timestamp", None)
    print(row["name"], json.dumps(attrs, ensure_ascii=False)[:400])

print("== attributes of one metric point ==")
row = conn.execute(
    "SELECT attrs FROM otel_metrics WHERE name='claude_code.token.usage' LIMIT 1"
).fetchone()
print(json.dumps(json.loads(row[0]), ensure_ascii=False, indent=2, sort_keys=True))
conn.close()
