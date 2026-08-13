"""Тесты приёмника телеметрии Claude Code (веха E, задача E2).

Посылки собраны по спецификации OTLP/JSON и по описанию метрик и событий из
https://code.claude.com/docs/en/monitoring-usage: целые едут строками, атрибуты
— парами key/value, имена полей в camelCase.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson
import pytest
from fastapi.testclient import TestClient

from cloudo_dash import config, metrics, paths
from cloudo_dash.analyzer import digest
from cloudo_dash.api.server import create_app
from cloudo_dash.collector import otlp
from cloudo_dash.db import connect

# Наносекунды: 2026-08-14T07:00:00Z и минутой позже.
START_NANO = "1786690800000000000"
END_NANO = "1786690860000000000"


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


def point(value: int, **extra: str | int) -> dict[str, Any]:
    return {
        "startTimeUnixNano": START_NANO,
        "timeUnixNano": END_NANO,
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


def event(name: str, sequence: int, **extra: str | int) -> dict[str, Any]:
    return {
        "timeUnixNano": END_NANO,
        "observedTimeUnixNano": END_NANO,
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


# --- разбор ------------------------------------------------------------------


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
    """Почта и идентификаторы аккаунта в БД не нужны: считаем расход, не человека."""
    payload = metrics_payload(point(1200, type="input", query_source="main"))
    payload["resourceMetrics"][0]["resource"]["attributes"].extend(
        attrs(user__email="кто-то@example.com", organization__id="c777", user__id="65c7")
    )
    otlp.ingest(conn, "metrics", payload)
    stored = json.loads(conn.execute("SELECT attrs FROM otel_metrics").fetchone()[0])
    assert set(stored) == {"session.id", "model", "type", "query_source"}


def test_repeated_batch_does_not_double_the_numbers(conn: Any) -> None:
    """Экспортёр повторяет неподтверждённую посылку — цифры от этого не растут."""
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
    """Смена формата не должна обрывать приём: непонятое считается потерей."""
    payload = metrics_payload(point(1200, type="input"), {"timeUnixNano": END_NANO}, "мусор")
    payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"].append({"name": "без данных"})
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
    # Решение по разрешению есть только здесь: в транскрипт оно не попадает.
    assert json.loads(rows[1]["attrs"])["decision"] == "accept"


def test_event_name_from_body_when_attribute_is_missing(conn: Any) -> None:
    record = event("api_error", 3)
    record["attributes"] = [item for item in record["attributes"] if item["key"] != "event.name"]
    assert otlp.ingest(conn, "logs", logs_payload(record))["stored"] == 1
    assert conn.execute("SELECT name FROM otel_events").fetchone()[0] == "api_error"


def test_snake_case_fields_are_understood(conn: Any) -> None:
    """OTLP/JSON разрешает и оригинальные имена полей protobuf."""
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


# --- эндпоинт ----------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "api.db"
    connect(db_path).close()  # схема: lifespan в тестах не запускается
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
    assert response.json() == {}  # так по протоколу выглядит успешный экспорт
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
    """Трассы (бета) подтверждаются: 4xx загнал бы экспортёр в повторы."""
    assert client.post("/otlp/v1/traces", json={"resourceSpans": []}).status_code == 200
    assert client.post("/otlp/v1/чего-то", json={}).status_code == 404


def test_broken_body_is_acknowledged(client: TestClient) -> None:
    response = client.post("/otlp/v1/metrics", content="не json".encode())
    assert response.status_code == 200
    assert stored_metrics(client) == 0


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


# --- что телеметрия даёт дашборду и советчику -------------------------------


def test_off_transcript_usage_is_counted_apart(conn: Any) -> None:
    """Служебные запросы считаются отдельно: в транскрипте их нет вовсе."""
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
    assert usage["share"] == 0.5  # половина того, что телеметрия видела за период
    assert usage["request_kinds"][0]["source"] == "generate_session_title"


def test_permission_decisions_are_split_by_source(conn: Any) -> None:
    """Ручное подтверждение останавливает работу, автоматическое — нет."""
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


def test_digest_marks_missing_telemetry(conn: Any) -> None:
    """Без телеметрии секции помечены прочерком: ноль подтверждений — не факт."""
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
    }


# --- занятость сессии по событиям -------------------------------------------

#: Сессия попросила инструмент в 07:00:00, смотрим на неё через полминуты.
NOW = datetime(2026, 8, 14, 7, 0, 30, tzinfo=UTC)


def moment(second: int) -> str:
    return datetime(2026, 8, 14, 7, 0, second, tzinfo=UTC).strftime(metrics.TS_FORMAT)


def waiting_row(**extra: Any) -> dict[str, Any]:
    """Сессия, которая попросила инструмент и с тех пор молчит."""
    return {
        "last_record_kind": "assistant",
        "last_stop_reason": "tool_use",
        "last_record_at": moment(0),
        "is_live": 1,
        "busy_since": None,
    } | extra


def test_decision_event_means_the_tool_is_running() -> None:
    """Решение по разрешению есть — сессия работает, а не ждёт человека."""
    row = waiting_row(otel_seen_at=moment(2), tool_decided_at=moment(2))
    assert metrics.session_status(row, NOW) == metrics.STATUS_WORKING


def test_silent_telemetry_means_waiting_for_a_human() -> None:
    """Телеметрия работает, решения нет — это висящий запрос разрешения.

    Раньше здесь смотрели на потомков процесса, и инструменты без своего
    процесса (MCP-вызовы, `WebFetch`) выглядели ожиданием ошибочно.
    """
    row = waiting_row(otel_seen_at=moment(1), busy_since=moment(1))
    assert metrics.session_status(row, NOW) == metrics.STATUS_PERMISSION


def test_without_telemetry_processes_still_decide() -> None:
    """Без телеметрии остаётся прежнее правило — по дереву процессов."""
    assert metrics.session_status(waiting_row(busy_since=moment(1)), NOW) == metrics.STATUS_WORKING
    assert metrics.session_status(waiting_row(), NOW) == metrics.STATUS_PERMISSION


def test_stale_telemetry_is_not_trusted() -> None:
    """Телеметрию выключили посреди работы — молчание перестаёт быть ответом."""
    stale = datetime(2026, 8, 14, 6, 0, tzinfo=UTC).strftime(metrics.TS_FORMAT)
    row = waiting_row(otel_seen_at=stale, busy_since=moment(1))
    assert metrics.session_status(row, NOW) == metrics.STATUS_WORKING
