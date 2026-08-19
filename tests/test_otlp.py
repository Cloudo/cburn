"""Tests of the Claude Code telemetry receiver (milestone E, task E2).

The payloads are built to the OTLP/JSON specification and to the description of metrics
and events from https://code.claude.com/docs/en/monitoring-usage: integers travel as
strings, attributes as key/value pairs, field names in camelCase.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import orjson
import pytest
from fastapi.testclient import TestClient

from cburn import config, metrics, paths
from cburn.analyzer import digest
from cburn.api import server
from cburn.api.server import create_app
from cburn.collector import otlp
from cburn.db import connect

# Nanoseconds: 2026-08-14T07:00:00Z and a minute later. Fixed - where the parsing
# itself is checked: the time format, deduplication, the bounds of a point window.
START_NANO = "1786690800000000000"
END_NANO = "1786690860000000000"

#: The same moment as a date: periods in the parsing tests are counted from it.
FIXED_DAY = datetime(2026, 8, 14, tzinfo=UTC)


def nanos(moment: datetime) -> str:
    """Time in nanoseconds, the way OTLP sends it."""
    return str(int(moment.timestamp() * 1_000_000_000))


def now_nanos() -> str:
    """Now - for tests that look at "today" slices.

    A payload nailed to a date used to fall inside today's window yesterday and no
    longer does today: such tests must live in the same day as the overview.
    """
    return nanos(datetime.now(UTC))


def attrs(**pairs: str | int) -> list[dict[str, Any]]:
    out = []
    for key, value in pairs.items():
        name = key.replace("__", ".")
        if isinstance(value, int):
            out.append({"key": name, "value": {"intValue": str(value)}})
        else:
            out.append({"key": name, "value": {"stringValue": value}})
    return out


def metrics_payload(*points: dict[str, Any], name: str = "claude_code.token.usage") -> dict:
    return {
        "resourceMetrics": [
            {
                "resource": {"attributes": attrs(service__name="claude-code")},
                "scopeMetrics": [
                    {
                        "scope": {"name": "com.anthropic.claude_code"},
                        "metrics": [
                            {
                                "name": name,
                                "unit": "tokens",
                                "sum": {
                                    "dataPoints": list(points),
                                    "aggregationTemporality": 1,
                                    "isMonotonic": True,
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }


def point(value: int, at: str | None = None, **extra: str | int) -> dict[str, Any]:
    return {
        "startTimeUnixNano": at or START_NANO,
        "timeUnixNano": at or END_NANO,
        "asInt": str(value),
        "attributes": attrs(session__id="s1", model="claude-opus-5", **extra),
    }


def logs_payload(*records: dict[str, Any]) -> dict:
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": attrs(service__name="claude-code")},
                "scopeLogs": [
                    {"scope": {"name": "com.anthropic.claude_code"}, "logRecords": list(records)}
                ],
            }
        ]
    }


def event(name: str, sequence: int, at: str | None = None, **extra: str | int) -> dict[str, Any]:
    return {
        "timeUnixNano": at or END_NANO,
        "observedTimeUnixNano": at or END_NANO,
        "severityNumber": 9,
        "severityText": "INFO",
        "body": {"stringValue": f"claude_code.{name}"},
        "attributes": attrs(event__name=name, event__sequence=sequence, session__id="s1", **extra),
    }


@pytest.fixture
def conn(tmp_path: Path) -> Any:
    connection = connect(tmp_path / "otel.db")
    yield connection
    connection.close()


# --- parsing -------------------------------------------------------------------


def test_metric_point_is_split_into_columns(conn: Any) -> None:
    payload = metrics_payload(point(1200, type="input"), point(90, type="output"))
    stats = otlp.ingest(conn, "metrics", payload)

    assert stats == {"seen": 2, "stored": 2, "dropped": 0}
    rows = conn.execute("SELECT * FROM otel_metrics ORDER BY kind").fetchall()
    assert [row["kind"] for row in rows] == ["input", "output"]
    assert [row["value"] for row in rows] == [1200.0, 90.0]
    assert {row["session_id"] for row in rows} == {"s1"}
    assert {row["model"] for row in rows} == {"claude-opus-5"}
    assert rows[0]["ts"] == "2026-08-14T07:01:00.000000Z"
    assert rows[0]["start_ts"] == "2026-08-14T07:00:00.000000Z"


def test_personal_attributes_are_not_stored(conn: Any) -> None:
    """The email and account ids are not needed in the database: we count spend, not people."""
    payload = metrics_payload(point(1200, type="input", query_source="main"))
    payload["resourceMetrics"][0]["resource"]["attributes"].extend(
        attrs(user__email="someone@example.com", organization__id="c777", user__id="65c7")
    )
    otlp.ingest(conn, "metrics", payload)
    stored = json.loads(conn.execute("SELECT attrs FROM otel_metrics").fetchone()[0])
    assert set(stored) == {"session.id", "model", "type", "query_source"}


def test_conversation_text_is_never_stored(conn: Any) -> None:
    """A human may switch OTEL_LOG_USER_PROMPTS on for their own debugging - the dashboard
    must not become a conversation store because of that (SPEC §7)."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("user_prompt", 1, prompt="the secret text of the task", prompt_length=64),
            event("assistant_response", 2, response="the whole model answer", response_length=41),
            event(
                "tool_result",
                3,
                tool_name="Bash",
                tool_input='{"command": "cat ~/.ssh/id_rsa"}',
                error="ENOENT: no such file",
            ),
        ),
    )
    stored = " ".join(
        row[0]
        for row in conn.execute("SELECT attrs FROM otel_events")  # type: ignore[misc]
    )
    assert "the secret text" not in stored
    assert "the model answer" not in stored
    assert "id_rsa" not in stored
    assert "ENOENT" not in stored
    # Lengths and names stay: everything we need is counted from them.
    assert "prompt_length" in stored
    assert "Bash" in stored


def test_nested_values_are_filtered_too(conn: Any) -> None:
    """Content must not seep through a nested list of pairs."""
    record = event("tool_result", 1, tool_name="Bash")
    record["attributes"].append(
        {
            "key": "details",
            "value": {
                "kvlistValue": {
                    "values": [
                        {"key": "response", "value": {"stringValue": "the answer text"}},
                        {"key": "duration_ms", "value": {"stringValue": "968"}},
                    ]
                }
            },
        }
    )
    otlp.ingest(conn, "logs", logs_payload(record))
    stored = conn.execute("SELECT attrs FROM otel_events").fetchone()[0]
    assert "the answer text" not in stored
    assert "968" in stored


def test_repeated_batch_does_not_double_the_numbers(conn: Any) -> None:
    """The exporter repeats an unacknowledged payload - the numbers must not grow."""
    payload = metrics_payload(point(1200, type="input"))
    assert otlp.ingest(conn, "metrics", payload)["stored"] == 1
    assert otlp.ingest(conn, "metrics", payload)["stored"] == 0
    assert conn.execute("SELECT COUNT(*) FROM otel_metrics").fetchone()[0] == 1


def test_same_metric_in_next_window_is_a_new_point(conn: Any) -> None:
    otlp.ingest(conn, "metrics", metrics_payload(point(1200, type="input")))
    later = point(300, type="input")
    later["startTimeUnixNano"] = END_NANO
    later["timeUnixNano"] = "1786690920000000000"
    otlp.ingest(conn, "metrics", metrics_payload(later))
    assert conn.execute("SELECT SUM(value) FROM otel_metrics").fetchone()[0] == 1500.0


def test_double_and_gauge_points_are_read(conn: Any) -> None:
    cost = {
        "startTimeUnixNano": START_NANO,
        "timeUnixNano": END_NANO,
        "asDouble": 0.0731,
        "attributes": attrs(session__id="s1", model="claude-haiku-4-5-20251001"),
    }
    payload = metrics_payload(cost, name="claude_code.cost.usage")
    payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["gauge"] = payload[
        "resourceMetrics"
    ][0]["scopeMetrics"][0]["metrics"][0].pop("sum")
    assert otlp.ingest(conn, "metrics", payload)["stored"] == 1
    row = conn.execute("SELECT name, value, kind FROM otel_metrics").fetchone()
    assert (row["name"], row["value"], row["kind"]) == ("claude_code.cost.usage", 0.0731, None)


def test_broken_pieces_do_not_lose_the_rest(conn: Any) -> None:
    """A format change must not cut reception off: what is not understood counts as a loss."""
    payload = metrics_payload(point(1200, type="input"), {"timeUnixNano": END_NANO}, "garbage")
    payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"].append({"name": "no data"})
    stats = otlp.ingest(conn, "metrics", payload)
    assert stats["stored"] == 1
    assert stats["dropped"] == 3
    assert conn.execute("SELECT dropped FROM otel_ingest").fetchone()[0] == 3


def test_events_keep_attributes_and_dedupe_by_sequence(conn: Any) -> None:
    payload = logs_payload(
        event("api_request", 17, model="claude-opus-5", cost_usd="0.0421", duration_ms=2310),
        event("tool_decision", 18, tool_name="Bash", decision="accept", source="config"),
    )
    assert otlp.ingest(conn, "logs", payload)["stored"] == 2
    assert otlp.ingest(conn, "logs", payload)["stored"] == 0

    rows = conn.execute("SELECT * FROM otel_events ORDER BY name").fetchall()
    assert [row["name"] for row in rows] == ["api_request", "tool_decision"]
    assert rows[0]["session_id"] == "s1"
    assert json.loads(rows[0]["attrs"])["duration_ms"] == 2310
    # The permission decision exists only here: it never reaches the transcript.
    assert json.loads(rows[1]["attrs"])["decision"] == "accept"


def test_event_name_from_body_when_attribute_is_missing(conn: Any) -> None:
    record = event("api_error", 3)
    record["attributes"] = [item for item in record["attributes"] if item["key"] != "event.name"]
    assert otlp.ingest(conn, "logs", logs_payload(record))["stored"] == 1
    assert conn.execute("SELECT name FROM otel_events").fetchone()[0] == "api_error"


def test_histogram_is_counted_as_a_loss(conn: Any) -> None:
    """Claude Code sends no histograms; if they appear, the loss counter will show it."""
    payload = metrics_payload(point(1), name="claude_code.some.histogram")
    metric = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
    metric["histogram"] = {"dataPoints": [metric.pop("sum")["dataPoints"][0]]}
    stats = otlp.ingest(conn, "metrics", payload)
    assert (stats["stored"], stats["dropped"]) == (0, 1)


def test_attribute_values_of_every_shape_are_read(conn: Any) -> None:
    """An OTLP `AnyValue` is not only a string: booleans, arrays, nested lists."""
    record = event("tool_result", 1)
    record["attributes"] += [
        {"key": "success", "value": {"boolValue": True}},
        {"key": "duration_ms", "value": {"intValue": "968"}},
        {"key": "ratio", "value": {"doubleValue": 0.5}},
        {
            "key": "workspace.host_paths",
            "value": {"arrayValue": {"values": [{"stringValue": "/tmp/x"}]}},
        },
        {
            "key": "nested",
            "value": {"kvlistValue": {"values": [{"key": "k", "value": {"stringValue": "v"}}]}},
        },
    ]
    otlp.ingest(conn, "logs", logs_payload(record))
    attrs = json.loads(conn.execute("SELECT attrs FROM otel_events").fetchone()[0])
    assert attrs["success"] is True
    assert attrs["duration_ms"] == 968
    assert attrs["ratio"] == 0.5
    assert attrs["workspace.host_paths"] == ["/tmp/x"]
    assert attrs["nested"] == {"k": "v"}


def test_events_without_a_session_are_still_stored(conn: Any) -> None:
    """An event without `session.id` is an event too: before the first prompt there are plenty."""
    record = event("session_start", 1)
    record["attributes"] = [item for item in record["attributes"] if item["key"] != "session.id"]
    assert otlp.ingest(conn, "logs", logs_payload(record))["stored"] == 1
    assert otlp.ingest(conn, "logs", logs_payload(record))["stored"] == 0  # the repeat is swallowed
    assert conn.execute("SELECT session_id FROM otel_events").fetchone()[0] is None


def test_record_time_falls_back_to_the_observed_one(conn: Any) -> None:
    """Some records arrive without a time of their own - then the reception time is used."""
    record = event("api_request", 1)
    record["timeUnixNano"] = "0"
    assert otlp.ingest(conn, "logs", logs_payload(record))["stored"] == 1
    assert conn.execute("SELECT ts FROM otel_events").fetchone()[0] == "2026-08-14T07:01:00.000000Z"


def test_several_resources_in_one_batch(conn: Any) -> None:
    """The exporter may put several resources and scopes into one payload."""
    payload = metrics_payload(point(10, type="input"))
    payload["resourceMetrics"].append(
        metrics_payload(point(20, type="output"))["resourceMetrics"][0]
    )
    assert otlp.ingest(conn, "metrics", payload)["stored"] == 2


def test_snake_case_fields_are_understood(conn: Any) -> None:
    """OTLP/JSON allows the original protobuf field names too."""
    payload = {
        "resource_metrics": [
            {
                "resource": {"attributes": attrs(service__name="claude-code")},
                "scope_metrics": [
                    {
                        "metrics": [
                            {
                                "name": "claude_code.active_time.total",
                                "sum": {
                                    "data_points": [
                                        {
                                            "start_time_unix_nano": START_NANO,
                                            "time_unix_nano": END_NANO,
                                            "as_double": 42.5,
                                            "attributes": attrs(session__id="s1", type="cli"),
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }
    assert otlp.ingest(conn, "metrics", payload)["stored"] == 1
    assert conn.execute("SELECT value, kind FROM otel_metrics").fetchone()["kind"] == "cli"


# --- endpoint ------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "api.db"
    connect(db_path).close()  # the schema: lifespan does not run in tests
    monkeypatch.setattr(paths, "CONFIG_PATH", tmp_path / "config.toml")
    app = create_app(
        db_path=db_path,
        projects_dir=tmp_path / "projects",
        watch=False,
        limits=None,
        liveness=lambda: None,
    )
    client = TestClient(app)
    client.db_path = db_path  # type: ignore[attr-defined]
    return client


def stored_metrics(client: TestClient) -> int:
    conn = connect(client.db_path, apply_schema=False)  # type: ignore[attr-defined]
    try:
        return int(conn.execute("SELECT COUNT(*) FROM otel_metrics").fetchone()[0])
    finally:
        conn.close()


def test_endpoint_accepts_metrics(client: TestClient) -> None:
    response = client.post("/otlp/v1/metrics", json=metrics_payload(point(1200, type="input")))
    assert response.status_code == 200
    assert response.json() == {}  # that is what a successful export looks like by protocol
    assert stored_metrics(client) == 1


def test_endpoint_accepts_gzip(client: TestClient) -> None:
    body = gzip.compress(orjson.dumps(metrics_payload(point(7, type="output"))))
    response = client.post(
        "/otlp/v1/metrics",
        content=body,
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert response.status_code == 200
    assert stored_metrics(client) == 1


def test_traces_are_acknowledged_but_not_stored(client: TestClient) -> None:
    """Traces (beta) are acknowledged: a 4xx would drive the exporter into retries."""
    assert client.post("/otlp/v1/traces", json={"resourceSpans": []}).status_code == 200
    assert client.post("/otlp/v1/whatever", json={}).status_code == 404


def test_broken_body_is_acknowledged(client: TestClient) -> None:
    response = client.post("/otlp/v1/metrics", content=b"not json")
    assert response.status_code == 200
    assert stored_metrics(client) == 0


def test_undecodable_batches_are_visible_in_status(client: TestClient) -> None:
    """With protocol=http/protobuf set, payloads will arrive and we will not understand them.

    The status must show that: "no payloads arrived" would send the human hunting in
    the wrong place.
    """
    client.post("/otlp/v1/metrics", content=b"\x00\x01protobuf")
    state = client.get("/api/otel").json()
    assert state["signals"]["metrics"] == {
        "last_at": state["signals"]["metrics"]["last_at"],
        "batches": 1,
        "stored": 0,
        "dropped": 1,
    }


def test_disabled_receiver_stores_nothing(client: TestClient) -> None:
    cfg = config.load()
    cfg["otel"]["enabled"] = False
    config.save(cfg)
    assert client.post("/otlp/v1/metrics", json=metrics_payload(point(5))).status_code == 200
    assert stored_metrics(client) == 0


def test_status_endpoint_shows_what_arrived(client: TestClient) -> None:
    client.post("/otlp/v1/metrics", json=metrics_payload(point(1200, type="input")))
    client.post("/otlp/v1/logs", json=logs_payload(event("api_request", 1)))
    state = client.get("/api/otel").json()
    assert state["enabled"] is True
    assert state["signals"]["metrics"]["stored"] == 1
    assert [row["name"] for row in state["metrics"]] == ["claude_code.token.usage"]
    assert [row["name"] for row in state["events"]] == ["api_request"]


def test_concurrent_batches_do_not_lock_the_database(client: TestClient) -> None:
    """Payloads arrive from different threads, the watcher and the cleanup write nearby.

    SQLite admits one writer at a time, so what matters is that parallel payloads do not
    break reception: the connection waits its turn instead of returning an error.
    """
    batches = [
        logs_payload(event("api_request", index, model="claude-opus-5")) for index in range(40)
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(
            pool.map(lambda body: client.post("/otlp/v1/logs", json=body).status_code, batches)
        )
    assert codes == [200] * len(batches)

    conn = connect(client.db_path, apply_schema=False)  # type: ignore[attr-defined]
    try:
        assert conn.execute("SELECT COUNT(*) FROM otel_events").fetchone()[0] == len(batches)
        # The reception counter matches the number of payloads: no increment was lost.
        assert conn.execute("SELECT batches FROM otel_ingest").fetchone()[0] == len(batches)
    finally:
        conn.close()


def test_locked_database_asks_for_a_retry(client: TestClient) -> None:
    """A refusal is more honest than a silent acknowledgement: on a 503 the exporter repeats
    the payload itself, while on a 200 the data would be gone."""
    with mock.patch.object(otlp, "ingest", side_effect=sqlite3.OperationalError("locked")):
        response = client.post("/otlp/v1/logs", json=logs_payload(event("api_request", 1)))
    assert response.status_code == 503

    # A repeat once the database is free goes through in the usual way.
    assert (
        client.post("/otlp/v1/logs", json=logs_payload(event("api_request", 1))).status_code == 200
    )


def test_parallel_writers_wait_for_each_other(tmp_path: Path) -> None:
    """Every request writes through its own connection from its own thread - exactly the
    way the server does it. SQLite has one writer, the rest wait their turn."""
    db_path = tmp_path / "parallel.db"
    connect(db_path).close()  # the schema

    def write(index: int) -> dict[str, int]:
        conn = connect(db_path, apply_schema=False)
        try:
            return otlp.ingest(conn, "logs", logs_payload(event("api_request", index)))
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(50)))

    assert sum(result["stored"] for result in results) == 50
    conn = connect(db_path, apply_schema=False)
    try:
        assert conn.execute("SELECT COUNT(*) FROM otel_events").fetchone()[0] == 50
    finally:
        conn.close()


# --- retention -----------------------------------------------------------------


def test_old_telemetry_is_pruned(conn: Any) -> None:
    """Events arrive several per turn - without a retention the database grows
    faster than the useful data."""
    otlp.ingest(conn, "metrics", metrics_payload(point(1200, type="input")))
    otlp.ingest(conn, "logs", logs_payload(event("api_request", 1)))
    now = datetime(2026, 9, 14, tzinfo=UTC)  # a month later

    assert otlp.prune(conn, keep_days=60, now=now) == {"metrics": 0, "events": 0}
    assert otlp.prune(conn, keep_days=7, now=now) == {"metrics": 1, "events": 1}
    assert conn.execute("SELECT COUNT(*) FROM otel_events").fetchone()[0] == 0


def test_pruning_keeps_recent_and_never_touches_turns(conn: Any) -> None:
    """The cleanup does not touch parser data: it has its own history and its own meaning."""
    with conn:
        conn.execute("INSERT INTO sessions (id) VALUES ('s1')")
        conn.execute(
            "INSERT INTO turns (message_id, session_id, ts) VALUES ('m1', 's1', ?)",
            ("2020-01-01T00:00:00.000000Z",),
        )
    otlp.ingest(conn, "logs", logs_payload(event("api_request", 1)))
    otlp.prune(conn, keep_days=1, now=datetime(2026, 8, 14, 7, 30, tzinfo=UTC))
    assert conn.execute("SELECT COUNT(*) FROM otel_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1


def test_zero_days_means_keep_everything(conn: Any) -> None:
    otlp.ingest(conn, "logs", logs_payload(event("api_request", 1)))
    otlp.prune(conn, keep_days=0, now=datetime(2030, 1, 1, tzinfo=UTC))
    assert conn.execute("SELECT COUNT(*) FROM otel_events").fetchone()[0] == 1


def test_status_reports_volume(conn: Any) -> None:
    """The volume shows whether the retention works and what the data costs."""
    otlp.ingest(conn, "metrics", metrics_payload(point(1200, type="input")))
    otlp.ingest(conn, "logs", logs_payload(event("api_request", 1)))
    stored = otlp.status(conn)["stored"]
    assert stored["rows"] == 2
    assert stored["bytes"] > 0
    assert stored["oldest"] == "2026-08-14T07:01:00.000000Z"


# --- what telemetry gives the dashboard and the advisor ------------------------


def test_off_transcript_usage_is_counted_apart(conn: Any) -> None:
    """Service requests are counted separately: they are not in the transcript at all."""
    otlp.ingest(
        conn,
        "metrics",
        metrics_payload(
            point(517, type="input", query_source="auxiliary"),
            point(18, type="output", query_source="auxiliary"),
            point(22846, type="cacheCreation", query_source="main"),
        ),
    )
    otlp.ingest(
        conn,
        "metrics",
        metrics_payload(
            point(1, query_source="auxiliary"),
            point(1, query_source="main"),
            name="claude_code.cost.usage",
        ),
    )
    otlp.ingest(
        conn, "logs", logs_payload(event("api_request", 1, query_source="generate_session_title"))
    )

    usage = metrics.otel_usage(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert usage["tokens"] == 535
    assert usage["input_tokens"] == 517
    assert usage["cost_usd"] == 1.0
    assert usage["share"] == 0.5  # half of what telemetry saw over the period
    assert usage["request_kinds"][0]["source"] == "generate_session_title"


def test_permission_decisions_are_split_by_source(conn: Any) -> None:
    """A manual confirmation stops the work, an automatic one does not."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("tool_decision", 1, tool_name="Bash", decision="accept", source="user_permanent"),
            event("tool_decision", 2, tool_name="Bash", decision="accept", source="user_temporary"),
            event("tool_decision", 3, tool_name="Edit", decision="reject", source="user_reject"),
            event("tool_decision", 4, tool_name="Read", decision="accept", source="config"),
        ),
    )
    stats = metrics.otel_permissions(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert (stats["decisions"], stats["manual"], stats["auto"]) == (4, 3, 1)
    assert stats["rejected"] == 1
    assert stats["by_tool"][0] == {"tool": "Bash", "decisions": 2}


def test_permission_mode_switches_are_counted(conn: Any) -> None:
    """Going into acceptEdits over and over means the permission rules are in the way."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("permission_mode_changed", 1, from_mode="default", to_mode="acceptEdits"),
            event("permission_mode_changed", 2, from_mode="acceptEdits", to_mode="default"),
            event("permission_mode_changed", 3, from_mode="default", to_mode="acceptEdits"),
        ),
    )
    stats = metrics.otel_permissions(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert stats["mode_switches"][0] == {"mode": "acceptEdits", "switches": 2}
    assert stats["decisions"] == 0  # a mode switch is not a decision about a tool


def test_permission_breakdown_has_a_tail_limit(conn: Any) -> None:
    """A machine can carry dozens of MCP tools: the tail eats the digest budget."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            *[
                event(
                    "tool_decision",
                    index,
                    tool_name=f"mcp__server__tool{index}",
                    decision="accept",
                    source="user_temporary",
                )
                for index in range(30)
            ]
        ),
    )
    stats = metrics.otel_permissions(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert stats["decisions"] == 30
    assert len(stats["by_tool"]) == metrics.PERMISSION_TOOLS
    built = digest.build(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert built["size"]["within_limit"]


def test_digest_carries_work_done(conn: Any) -> None:
    """The advisor needs not only the spend but also what was done for it."""
    otlp.ingest(
        conn,
        "metrics",
        metrics_payload(point(600, type="user"), name="claude_code.active_time.total"),
    )
    otlp.ingest(
        conn,
        "metrics",
        metrics_payload(point(120, type="added"), name="claude_code.lines_of_code.count"),
    )
    built = digest.build(conn, datetime(2026, 8, 14, tzinfo=UTC))["off_transcript"]
    assert built["available"] is True
    assert built["active_minutes"] == 10.0
    assert built["lines_added"] == 120


def test_digest_marks_missing_telemetry(conn: Any) -> None:
    """Without telemetry the sections are marked with a dash: zero confirmations is not a fact."""
    built = digest.build(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert built["permissions"]["available"] is False
    assert built["off_transcript"]["available"] is False


def test_digest_carries_permissions_when_telemetry_works(conn: Any) -> None:
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("tool_decision", 1, tool_name="Bash", decision="accept", source="user_permanent")
        ),
    )
    built = digest.build(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert built["permissions"] == {
        "available": True,
        "decisions": 1,
        "manual": 1,
        "auto": 0,
        "rejected": 0,
        "by_tool": [{"tool": "Bash", "decisions": 1}],
        "mode_switches": [],
    }


# --- session busyness by events ------------------------------------------------

#: The session asked for a tool at 07:00:00, we look at it half a minute later.
NOW = datetime(2026, 8, 14, 7, 0, 30, tzinfo=UTC)


def moment(second: int) -> str:
    return datetime(2026, 8, 14, 7, 0, second, tzinfo=UTC).strftime(metrics.TS_FORMAT)


def waiting_row(**extra: Any) -> dict[str, Any]:
    """A session that asked for a tool and has been quiet ever since."""
    return {
        "last_record_kind": "assistant",
        "last_stop_reason": "tool_use",
        "last_record_at": moment(0),
        "is_live": 1,
        "busy_since": None,
    } | extra


def test_decision_event_means_the_tool_is_running() -> None:
    """There is a permission decision - the session works rather than waits for the human."""
    row = waiting_row(otel_seen_at=moment(2), tool_decided_at=moment(2))
    assert metrics.session_status(row, NOW) == metrics.STATUS_WORKING


def test_silent_telemetry_means_waiting_for_a_human() -> None:
    """Telemetry works, there is no decision - this is a hanging permission request.

    Previously the process children were consulted here, and tools without a process
    of their own (MCP calls, `WebFetch`) looked like waiting by mistake.
    """
    row = waiting_row(otel_seen_at=moment(1), busy_since=moment(1))
    assert metrics.session_status(row, NOW) == metrics.STATUS_PERMISSION


def test_without_telemetry_processes_still_decide() -> None:
    """Without telemetry the old rule remains - by the process tree."""
    assert metrics.session_status(waiting_row(busy_since=moment(1)), NOW) == metrics.STATUS_WORKING
    assert metrics.session_status(waiting_row(), NOW) == metrics.STATUS_PERMISSION


def test_stale_telemetry_is_not_trusted() -> None:
    """Telemetry was switched off mid-work - silence stops being an answer."""
    stale = datetime(2026, 8, 14, 6, 0, tzinfo=UTC).strftime(metrics.TS_FORMAT)
    row = waiting_row(otel_seen_at=stale, busy_since=moment(1))
    assert metrics.session_status(row, NOW) == metrics.STATUS_WORKING


def test_decision_before_the_request_does_not_count() -> None:
    """A decision about the previous tool does not allow the next one."""
    past = datetime(2026, 8, 14, 6, 59, tzinfo=UTC).strftime(metrics.TS_FORMAT)
    row = waiting_row(otel_seen_at=moment(1), tool_decided_at=past)
    assert metrics.session_status(row, NOW) == metrics.STATUS_PERMISSION


def test_fresh_request_is_never_a_permission_prompt() -> None:
    """For the first seconds the tool is simply running: events travel in batches every
    few seconds, and the permission decision may still be on its way."""
    just_now = datetime(2026, 8, 14, 7, 0, 5, tzinfo=UTC)
    row = waiting_row(otel_seen_at=moment(1))
    assert metrics.session_status(row, just_now) == metrics.STATUS_WORKING


# --- the telemetry slice in the overview and the filters -----------------------


def test_overview_carries_telemetry(client: TestClient) -> None:
    at = now_nanos()
    client.post(
        "/otlp/v1/metrics", json=metrics_payload(point(517, at=at, query_source="auxiliary"))
    )
    client.post(
        "/otlp/v1/logs",
        json=logs_payload(
            event(
                "tool_decision",
                1,
                at=at,
                tool_name="Bash",
                decision="accept",
                source="user_permanent",
            )
        ),
    )
    otel = client.get("/api/overview").json()["otel"]
    assert otel["active"] is True
    assert otel["permissions"]["manual"] == 1


def test_overview_reuses_the_telemetry_slice(client: TestClient) -> None:
    """The overview goes to subscribers every second, while the telemetry slice is counted
    over tens of thousands of events - recomputing it on every tick is pointless."""
    at = now_nanos()
    client.post("/otlp/v1/logs", json=logs_payload(event("api_request", 1, at=at)))

    # The lifetime is set explicitly: relying on real five seconds is not an option -
    # under the load of a full run the requests drift further apart, and a "fresh"
    # slice manages to go stale between two lines of the test.
    with mock.patch.object(server, "OTEL_CACHE_SECONDS", 3600):
        assert client.get("/api/overview").json()["otel"]["active"] is True
        # A new event no longer makes it into the ready slice: it is still fresh.
        client.post(
            "/otlp/v1/logs", json=logs_payload(event("api_error", 2, at=at, status_code="429"))
        )
        assert client.get("/api/overview").json()["otel"]["api"]["errors"] == 0

    with mock.patch.object(server, "OTEL_CACHE_SECONDS", 0):
        assert client.get("/api/overview").json()["otel"]["api"]["errors"] == 1


def test_overview_without_telemetry_says_so(client: TestClient) -> None:
    otel = client.get("/api/overview").json()["otel"]
    assert otel["active"] is False
    assert otel["last_at"] is None
    assert otel["off_transcript"]["tokens"] == 0
    assert otel["permissions"]["decisions"] == 0
    assert otel["api"] == {"errors": 0, "by_status": [], "internal": []}


def test_period_bounds_are_respected(conn: Any) -> None:
    """A point older than the period does not count: the overview starts at local midnight."""
    otlp.ingest(conn, "metrics", metrics_payload(point(517, query_source="auxiliary")))
    later = datetime(2026, 8, 14, 8, tzinfo=UTC)
    assert metrics.otel_usage(conn, later)["tokens"] == 0
    earlier = datetime(2026, 8, 14, 6, tzinfo=UTC)
    assert metrics.otel_usage(conn, earlier, until=later)["tokens"] == 517


def test_project_filter_narrows_telemetry(conn: Any) -> None:
    """The project filter works here too: the sessions are the same as for the other numbers."""
    with conn:
        conn.execute("INSERT INTO projects (id, slug) VALUES (1, '-Users-x-first')")
        conn.execute("INSERT INTO sessions (id, project_id) VALUES ('s1', 1)")
    otlp.ingest(conn, "metrics", metrics_payload(point(517, query_source="auxiliary")))
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("tool_decision", 1, tool_name="Bash", decision="accept", source="user_permanent")
        ),
    )
    since = datetime(2026, 8, 14, tzinfo=UTC)
    assert metrics.otel_usage(conn, since, project="first")["tokens"] == 517
    assert metrics.otel_usage(conn, since, project="second")["tokens"] == 0
    assert metrics.otel_permissions(conn, since, project="first")["manual"] == 1
    assert metrics.otel_permissions(conn, since, project="second")["manual"] == 0

    # A per-project digest must not mix its own numbers with machine-wide ones in one
    # section: active time is filtered the same way as spend.
    otlp.ingest(
        conn,
        "metrics",
        metrics_payload(point(90, type="user"), name="claude_code.active_time.total"),
    )
    assert metrics.otel_work(conn, since, project="first")["active_seconds"] == 90
    assert metrics.otel_work(conn, since, project="second")["active_seconds"] == 0
    built = digest.build(conn, since, project="second")["off_transcript"]
    assert built["available"] is False


def test_mcp_startup_cost_is_counted(conn: Any) -> None:
    """A server starts anew in every session, even if it was never called."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event(
                "mcp_server_connection",
                1,
                status="connected",
                duration_ms="1750",
                transport_type="stdio",
                **{"plugin.name": "playwright"},
            ),
            event(
                "mcp_server_connection",
                2,
                status="connected",
                duration_ms="2190",
                **{"plugin.name": "chrome-devtools-mcp"},
            ),
            # A disconnect carries the same field but means the lifetime lived: adding
            # it to the connection time would lie fourfold.
            event(
                "mcp_server_connection",
                3,
                status="disconnected",
                duration_ms="66329",
                **{"plugin.name": "playwright"},
            ),
            event(
                "mcp_server_connection",
                4,
                status="failed",
                duration_ms="120",
                error_code="ENOENT",
                **{"plugin.name": "broken"},
            ),
        ),
    )
    stats = metrics.otel_mcp(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert stats["connect_seconds"] == pytest.approx(3.94)
    assert stats["seconds_per_session"] == pytest.approx(3.94)  # all events from one session
    assert stats["failures"] == 1
    assert stats["servers"][0] == {
        "server": "chrome-devtools-mcp",
        "connects": 1,
        "failures": 0,
        "seconds": 2.19,
    }


def test_digest_carries_mcp_startup_cost(conn: Any) -> None:
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event(
                "mcp_server_connection",
                1,
                status="connected",
                duration_ms="1750",
                **{"plugin.name": "playwright"},
            )
        ),
    )
    built = digest.build(conn, datetime(2026, 8, 14, tzinfo=UTC))["mcp"]
    assert built["connections"]["connect_seconds"] == pytest.approx(1.75)
    assert built["connections"]["servers"][0]["server"] == "playwright"


def test_loaded_plugins_show_what_they_bring(conn: Any) -> None:
    """A plugin is free, its MCP server and skills are not: they load every time."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("plugin_loaded", 1, has_mcp="true", skill_path_count=0, **{"plugin.name": "pw"}),
            # The same plugin in another session - it stays one row in the list.
            event("plugin_loaded", 2, has_mcp="true", skill_path_count=0, **{"plugin.name": "pw"}),
            event(
                "plugin_loaded",
                3,
                has_mcp="false",
                skill_path_count=1,
                command_path_count=2,
                **{"plugin.name": "design"},
            ),
        ),
    )
    plugins = metrics.otel_plugins(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert [row["plugin"] for row in plugins] == ["design", "pw"]
    assert plugins[0] == {"plugin": "design", "mcp": 0, "hooks": 0, "skills": 1, "commands": 2}
    assert plugins[1]["mcp"] == 1

    built = digest.build(conn, datetime(2026, 8, 14, tzinfo=UTC))["mcp"]
    assert [row["plugin"] for row in built["plugins"]] == ["design", "pw"]


def test_digest_omits_mcp_connections_without_telemetry(conn: Any) -> None:
    """Without telemetry the section is absent - an empty list would read as "no servers"."""
    assert "connections" not in digest.build(conn, datetime(2026, 8, 14, tzinfo=UTC))["mcp"]


def test_slash_commands_are_counted(conn: Any) -> None:
    """In the transcript a slash command leaves markup rather than a name: the parser does
    not unpack it, telemetry names the command directly."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("user_prompt", 1, prompt_length=64),
            event(
                "user_prompt", 2, prompt_length=12, command_name="clear", command_source="builtin"
            ),
            event(
                "user_prompt", 3, prompt_length=14, command_name="clear", command_source="builtin"
            ),
            event(
                "user_prompt", 4, prompt_length=30, command_name="deploy", command_source="custom"
            ),
        ),
    )
    stats = metrics.otel_prompts(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert stats["prompts"] == 4
    assert stats["avg_length"] == 30.0
    assert stats["commands"][0] == {"command": "clear", "source": "builtin", "calls": 2}
    assert len(stats["commands"]) == 2  # a prompt without a command does not enter the list

    built = digest.build(conn, datetime(2026, 8, 14, tzinfo=UTC))["off_transcript"]
    assert built["prompts"]["commands"][0]["command"] == "clear"


def test_hook_time_is_counted(conn: Any) -> None:
    """A hook runs between turns: in the transcript there is just a pause in its place,
    and an HTTP hook to an unreachable service is indistinguishable from model thinking."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event(
                "hook_execution_complete",
                1,
                hook_event="UserPromptSubmit",
                total_duration_ms="15857",
                num_success="1",
            ),
            event(
                "hook_execution_complete",
                2,
                hook_event="Stop",
                total_duration_ms="34513",
                num_cancelled="1",
            ),
            event(
                "hook_execution_complete",
                3,
                hook_event="PreToolUse",
                total_duration_ms="6",
            ),
            # The start of execution does not know the time yet - completions are what count.
            event("hook_execution_start", 4, hook_event="Stop"),
        ),
    )
    stats = metrics.otel_hooks(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert stats["seconds"] == pytest.approx(50.376)
    assert stats["failures"] == 1
    assert stats["events"][0]["event"] == "Stop"
    assert stats["events"][0]["slowest"] == pytest.approx(34.513)

    built = digest.build(conn, datetime(2026, 8, 14, tzinfo=UTC))["off_transcript"]
    assert built["hooks"]["events"][0]["event"] == "Stop"


def test_registered_hooks_are_listed_once(conn: Any) -> None:
    """Hooks are registered anew in every session - unique pairs are counted."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("hook_registered", 1, hook_event="SessionStart", hook_type="http"),
            event("hook_registered", 2, hook_event="Stop", hook_type="http"),
            event("hook_registered", 3, hook_event="Stop", hook_type="http"),  # the second session
            event("hook_registered", 4, hook_event="PreToolUse", hook_type="command"),
        ),
    )
    registered = metrics.otel_hooks(conn, datetime(2026, 8, 14, tzinfo=UTC))["registered"]
    assert registered == [
        {"event": "PreToolUse", "type": "command"},
        {"event": "SessionStart", "type": "http"},
        {"event": "Stop", "type": "http"},
    ]


def test_session_starts_are_labelled(conn: Any) -> None:
    """`start_type` marks the starts: the transcript carries no such marking,
    there a continuation is visible only through copied turns."""
    name = "claude_code.session.count"
    otlp.ingest(conn, "metrics", metrics_payload(point(1, start_type="fresh"), name=name))
    otlp.ingest(conn, "metrics", metrics_payload(point(1, start_type="resume"), name=name))
    otlp.ingest(conn, "metrics", metrics_payload(point(1, start_type="continue"), name=name))
    with conn:
        conn.execute("INSERT INTO sessions (id) VALUES ('s1')")
        conn.execute(
            "INSERT INTO turns (message_id, session_id, ts) VALUES ('m1', 's1', ?)",
            ("2026-08-14T07:30:00.000000Z",),
        )

    counts = metrics.otel_sessions(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert counts["resumed"] == 2
    assert counts["telemetry"] == 1  # all the points are about one session
    assert counts["transcripts"] == 1
    assert {row["start_type"] for row in counts["starts"]} == {"fresh", "resume", "continue"}


def test_work_done_is_counted_from_metrics(conn: Any) -> None:
    """Lines of code and active time are counted by Claude Code itself - history has neither."""
    lines = "claude_code.lines_of_code.count"
    active = "claude_code.active_time.total"
    otlp.ingest(conn, "metrics", metrics_payload(point(120, type="added"), name=lines))
    otlp.ingest(conn, "metrics", metrics_payload(point(35, type="removed"), name=lines))
    otlp.ingest(conn, "metrics", metrics_payload(point(90, type="user"), name=active))
    otlp.ingest(conn, "metrics", metrics_payload(point(210, type="cli"), name=active))

    work = metrics.otel_work(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert work["lines_added"] == 120
    assert work["lines_removed"] == 35
    # Active time is the sum of both kinds: the human at the keyboard and the CLI work.
    assert work["active_seconds"] == 300
    assert work["waiting_seconds"] == 210


def test_api_failures_are_visible_only_here(conn: Any) -> None:
    """A failed request never reaches the transcript: there is only the final answer there."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("api_error", 1, status_code="429", attempt=1),
            event("api_error", 2, status_code="429", attempt=2),
            event("api_error", 3, status_code="529", attempt=1),
            event("api_refusal", 4),
            event("api_request", 5),  # a successful request does not count as an error
        ),
    )
    stats = metrics.otel_errors(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert stats["errors"] == 4
    assert stats["by_status"][0] == {"status": "429", "errors": 2}
    assert {row["status"] for row in stats["by_status"]} == {"429", "529", "—"}
    assert stats["internal"] == []


def test_client_failures_are_counted_apart(conn: Any) -> None:
    """A failure inside the client is another trouble than a network refusal: work breaks off."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("internal_error", 1, error_name="TypeError"),
            event("internal_error", 2, error_name="TypeError"),
            event("internal_error", 3, error_name="SyntaxError", error_code="ENOENT"),
            event("api_error", 4, status_code="429"),
        ),
    )
    stats = metrics.otel_errors(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert stats["errors"] == 1  # a network refusal is counted by its own counter
    assert stats["internal"] == [
        {"error": "TypeError", "count": 2},
        {"error": "SyntaxError", "count": 1},
    ]


def test_tool_times_come_from_events(conn: Any) -> None:
    """Time inside a tool exists only in telemetry: in the transcript the wait for a
    permission lies between the request and the result."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("tool_result", 1, tool_name="Bash", duration_ms="968", success="true"),
            event("tool_result", 2, tool_name="Bash", duration_ms="4032", success="false"),
            event("tool_result", 3, tool_name="Read", duration_ms="12", success="true"),
        ),
    )
    rows = metrics.session_tool_times(conn, "s1")
    assert [row["tool"] for row in rows] == ["Bash", "Read"]  # ordered by time
    assert rows[0]["calls"] == 2
    assert rows[0]["seconds"] == 5.0
    assert rows[0]["slowest"] == 4.032
    assert rows[0]["failures"] == 1
    assert metrics.session_tool_times(conn, "no-such") == []


def test_session_endpoint_carries_tool_times(client: TestClient) -> None:
    conn = connect(client.db_path, apply_schema=False)  # type: ignore[attr-defined]
    try:
        with conn:
            conn.execute("INSERT INTO sessions (id) VALUES ('s1')")
        otlp.ingest(
            conn,
            "logs",
            logs_payload(event("tool_result", 1, tool_name="Bash", duration_ms="968")),
        )
    finally:
        conn.close()
    times = client.get("/api/sessions/s1").json()["tool_times"]
    assert times == [
        {"tool": "Bash", "calls": 1, "seconds": 0.968, "slowest": 0.968, "failures": 0}
    ]


def test_session_list_reads_the_decision_from_the_database(conn: Any) -> None:
    """An end-to-end check: an event in the database reaches the status in the session list."""
    asked = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, last_at, last_record_kind, last_record_at,"
            " last_stop_reason, is_live) VALUES ('s1', ?, 'assistant', ?, 'tool_use', 1)",
            (asked.strftime(metrics.TS_FORMAT), asked.strftime(metrics.TS_FORMAT)),
        )
    now = asked + timedelta(seconds=30)

    rows = metrics.live_sessions(conn, now)
    assert [row["status"] for row in rows] == [metrics.STATUS_PERMISSION]

    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("tool_decision", 1, tool_name="Bash", decision="accept", source="config")
        ),
    )
    rows = metrics.live_sessions(conn, now)
    assert [row["status"] for row in rows] == [metrics.STATUS_WORKING]
    assert metrics.sessions_page(conn, now=now)[0]["status"] == metrics.STATUS_WORKING
