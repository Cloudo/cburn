"""Тесты приёмника телеметрии Claude Code (веха E, задача E2).

Посылки собраны по спецификации OTLP/JSON и по описанию метрик и событий из
https://code.claude.com/docs/en/monitoring-usage: целые едут строками, атрибуты
— парами key/value, имена полей в camelCase.
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

from cloudo_dash import config, metrics, paths
from cloudo_dash.analyzer import digest
from cloudo_dash.api import server
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


def test_conversation_text_is_never_stored(conn: Any) -> None:
    """Человек мог включить OTEL_LOG_USER_PROMPTS для своей отладки — дашборд
    от этого не должен становиться хранилищем переписки (ТЗ §7)."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("user_prompt", 1, prompt="секретный текст задания", prompt_length=64),
            event("assistant_response", 2, response="ответ модели целиком", response_length=41),
            event(
                "tool_result",
                3,
                tool_name="Bash",
                tool_input='{"command": "cat ~/.ssh/id_rsa"}',
                error="ENOENT: нет такого файла",
            ),
        ),
    )
    stored = " ".join(
        row[0]
        for row in conn.execute("SELECT attrs FROM otel_events")  # type: ignore[misc]
    )
    assert "секретный текст" not in stored
    assert "ответ модели" not in stored
    assert "id_rsa" not in stored
    assert "ENOENT" not in stored
    # Длины и имена остаются: по ним и считается всё, что нам нужно.
    assert "prompt_length" in stored
    assert "Bash" in stored


def test_nested_values_are_filtered_too(conn: Any) -> None:
    """Содержимое не должно просочиться через вложенный список пар."""
    record = event("tool_result", 1, tool_name="Bash")
    record["attributes"].append(
        {
            "key": "details",
            "value": {
                "kvlistValue": {
                    "values": [
                        {"key": "response", "value": {"stringValue": "текст ответа"}},
                        {"key": "duration_ms", "value": {"stringValue": "968"}},
                    ]
                }
            },
        }
    )
    otlp.ingest(conn, "logs", logs_payload(record))
    stored = conn.execute("SELECT attrs FROM otel_events").fetchone()[0]
    assert "текст ответа" not in stored
    assert "968" in stored


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


def test_histogram_is_counted_as_a_loss(conn: Any) -> None:
    """Гистограмм Claude Code не шлёт; появятся — увидим по счётчику потерь."""
    payload = metrics_payload(point(1), name="claude_code.some.histogram")
    metric = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
    metric["histogram"] = {"dataPoints": [metric.pop("sum")["dataPoints"][0]]}
    stats = otlp.ingest(conn, "metrics", payload)
    assert (stats["stored"], stats["dropped"]) == (0, 1)


def test_attribute_values_of_every_shape_are_read(conn: Any) -> None:
    """OTLP `AnyValue` бывает не только строкой: булево, массив, вложенный список."""
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
    """Событие без `session.id` — тоже событие: до первого промпта их хватает."""
    record = event("session_start", 1)
    record["attributes"] = [item for item in record["attributes"] if item["key"] != "session.id"]
    assert otlp.ingest(conn, "logs", logs_payload(record))["stored"] == 1
    assert otlp.ingest(conn, "logs", logs_payload(record))["stored"] == 0  # повтор гасится
    assert conn.execute("SELECT session_id FROM otel_events").fetchone()[0] is None


def test_record_time_falls_back_to_the_observed_one(conn: Any) -> None:
    """Часть записей приезжает без своего времени — тогда берётся время приёма."""
    record = event("api_request", 1)
    record["timeUnixNano"] = "0"
    assert otlp.ingest(conn, "logs", logs_payload(record))["stored"] == 1
    assert conn.execute("SELECT ts FROM otel_events").fetchone()[0] == "2026-08-14T07:01:00.000000Z"


def test_several_resources_in_one_batch(conn: Any) -> None:
    """Экспортёр вправе сложить в посылку несколько ресурсов и скоупов."""
    payload = metrics_payload(point(10, type="input"))
    payload["resourceMetrics"].append(
        metrics_payload(point(20, type="output"))["resourceMetrics"][0]
    )
    assert otlp.ingest(conn, "metrics", payload)["stored"] == 2


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


def test_undecodable_batches_are_visible_in_status(client: TestClient) -> None:
    """Если выставить protocol=http/protobuf, посылки пойдут, а мы их не поймём.

    Статус обязан это показать: «посылок не было» отправило бы человека искать
    проблему не там.
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
    """Посылки приходят из разных потоков, рядом пишут watcher и чистка.

    SQLite пускает одного писателя за раз, поэтому важно, что параллельные
    посылки не роняют приём: соединение ждёт своей очереди, а не отдаёт ошибку.
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
        # Счётчик приёмов сходится с числом посылок: инкремент не потерялся.
        assert conn.execute("SELECT batches FROM otel_ingest").fetchone()[0] == len(batches)
    finally:
        conn.close()


def test_locked_database_asks_for_a_retry(client: TestClient) -> None:
    """Отказ честнее молчаливого подтверждения: по 503 экспортёр повторит
    посылку сам, а по 200 данные пропали бы."""
    with mock.patch.object(otlp, "ingest", side_effect=sqlite3.OperationalError("locked")):
        response = client.post("/otlp/v1/logs", json=logs_payload(event("api_request", 1)))
    assert response.status_code == 503

    # Повтор после того, как база освободилась, проходит обычным порядком.
    assert (
        client.post("/otlp/v1/logs", json=logs_payload(event("api_request", 1))).status_code == 200
    )


def test_parallel_writers_wait_for_each_other(tmp_path: Path) -> None:
    """Каждый запрос пишет своим соединением из своего потока — ровно так же,
    как это делает сервер. Писатель в SQLite один, остальные ждут очереди."""
    db_path = tmp_path / "parallel.db"
    connect(db_path).close()  # схема

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


# --- срок хранения -----------------------------------------------------------


def test_old_telemetry_is_pruned(conn: Any) -> None:
    """Событий приходит по несколько на ход — без срока хранения БД растёт
    быстрее полезных данных."""
    otlp.ingest(conn, "metrics", metrics_payload(point(1200, type="input")))
    otlp.ingest(conn, "logs", logs_payload(event("api_request", 1)))
    now = datetime(2026, 9, 14, tzinfo=UTC)  # месяц спустя

    assert otlp.prune(conn, keep_days=60, now=now) == {"metrics": 0, "events": 0}
    assert otlp.prune(conn, keep_days=7, now=now) == {"metrics": 1, "events": 1}
    assert conn.execute("SELECT COUNT(*) FROM otel_events").fetchone()[0] == 0


def test_pruning_keeps_recent_and_never_touches_turns(conn: Any) -> None:
    """Чистка не трогает данные парсера: у них своя история и свой смысл."""
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
    """По объёму видно, работает ли срок хранения и во что данные обходятся."""
    otlp.ingest(conn, "metrics", metrics_payload(point(1200, type="input")))
    otlp.ingest(conn, "logs", logs_payload(event("api_request", 1)))
    stored = otlp.status(conn)["stored"]
    assert stored["rows"] == 2
    assert stored["bytes"] > 0
    assert stored["oldest"] == "2026-08-14T07:01:00.000000Z"


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


def test_permission_mode_switches_are_counted(conn: Any) -> None:
    """Уход в acceptEdits раз за разом значит, что правила разрешений мешают."""
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
    assert stats["decisions"] == 0  # переключение режима — не решение по инструменту


def test_permission_breakdown_has_a_tail_limit(conn: Any) -> None:
    """MCP-инструментов на машине бывают десятки: хвост ест бюджет дайджеста."""
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
    """Советчику нужен не только расход, но и что за него сделано."""
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
        "mode_switches": [],
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


def test_decision_before_the_request_does_not_count() -> None:
    """Решение по прошлому инструменту не разрешает следующий."""
    past = datetime(2026, 8, 14, 6, 59, tzinfo=UTC).strftime(metrics.TS_FORMAT)
    row = waiting_row(otel_seen_at=moment(1), tool_decided_at=past)
    assert metrics.session_status(row, NOW) == metrics.STATUS_PERMISSION


def test_fresh_request_is_never_a_permission_prompt() -> None:
    """Первые секунды инструмент просто работает: события едут пачкой раз в
    несколько секунд, и решение по разрешению может быть ещё в пути."""
    just_now = datetime(2026, 8, 14, 7, 0, 5, tzinfo=UTC)
    row = waiting_row(otel_seen_at=moment(1))
    assert metrics.session_status(row, just_now) == metrics.STATUS_WORKING


# --- срез телеметрии в обзоре и фильтры -------------------------------------


def test_overview_carries_telemetry(client: TestClient) -> None:
    client.post("/otlp/v1/metrics", json=metrics_payload(point(517, query_source="auxiliary")))
    client.post(
        "/otlp/v1/logs",
        json=logs_payload(
            event("tool_decision", 1, tool_name="Bash", decision="accept", source="user_permanent")
        ),
    )
    otel = client.get("/api/overview").json()["otel"]
    assert otel["active"] is True
    assert otel["permissions"]["manual"] == 1


def test_overview_reuses_the_telemetry_slice(client: TestClient) -> None:
    """Обзор уходит подписчикам каждую секунду, а срез телеметрии считается по
    десяткам тысяч событий — пересчитывать его на каждый тик незачем."""
    client.post("/otlp/v1/logs", json=logs_payload(event("api_request", 1)))
    first = client.get("/api/overview").json()["otel"]
    assert first["active"] is True

    # Новое событие в ту же секунду ещё не видно: срез свежий.
    client.post("/otlp/v1/logs", json=logs_payload(event("api_error", 2, status_code="429")))
    assert client.get("/api/overview").json()["otel"]["api"]["errors"] == 0

    with mock.patch.object(server, "OTEL_CACHE_SECONDS", 0):
        assert client.get("/api/overview").json()["otel"]["api"]["errors"] == 1


def test_overview_without_telemetry_says_so(client: TestClient) -> None:
    otel = client.get("/api/overview").json()["otel"]
    assert otel["active"] is False
    assert otel["last_at"] is None
    assert otel["off_transcript"]["tokens"] == 0
    assert otel["permissions"]["decisions"] == 0
    assert otel["api"] == {"errors": 0, "by_status": []}


def test_period_bounds_are_respected(conn: Any) -> None:
    """Точка старше периода в счёт не идёт: обзор считает с местной полуночи."""
    otlp.ingest(conn, "metrics", metrics_payload(point(517, query_source="auxiliary")))
    later = datetime(2026, 8, 14, 8, tzinfo=UTC)
    assert metrics.otel_usage(conn, later)["tokens"] == 0
    earlier = datetime(2026, 8, 14, 6, tzinfo=UTC)
    assert metrics.otel_usage(conn, earlier, until=later)["tokens"] == 517


def test_project_filter_narrows_telemetry(conn: Any) -> None:
    """Фильтр по проекту работает и здесь: сессии те же, что у остальных цифр."""
    with conn:
        conn.execute("INSERT INTO projects (id, slug) VALUES (1, '-Users-x-первый')")
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
    assert metrics.otel_usage(conn, since, project="первый")["tokens"] == 517
    assert metrics.otel_usage(conn, since, project="второй")["tokens"] == 0
    assert metrics.otel_permissions(conn, since, project="первый")["manual"] == 1
    assert metrics.otel_permissions(conn, since, project="второй")["manual"] == 0

    # Дайджест по проекту не должен смешивать в одной секции свои цифры с
    # общемашинными: активное время фильтруется так же, как расход.
    otlp.ingest(
        conn,
        "metrics",
        metrics_payload(point(90, type="user"), name="claude_code.active_time.total"),
    )
    assert metrics.otel_work(conn, since, project="первый")["active_seconds"] == 90
    assert metrics.otel_work(conn, since, project="второй")["active_seconds"] == 0
    built = digest.build(conn, since, project="второй")["off_transcript"]
    assert built["available"] is False


def test_mcp_startup_cost_is_counted(conn: Any) -> None:
    """Сервер стартует заново в каждой сессии, даже если его не позвали."""
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
            # Отключение несёт то же поле, но означает прожитое время: сложить
            # его со временем подключения значило бы соврать вчетверо.
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
                **{"plugin.name": "сломанный"},
            ),
        ),
    )
    stats = metrics.otel_mcp(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert stats["connect_seconds"] == pytest.approx(3.94)
    assert stats["seconds_per_session"] == pytest.approx(3.94)  # все события одной сессии
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
    """Плагин бесплатен, а его MCP-сервер и скиллы — нет: они грузятся всегда."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("plugin_loaded", 1, has_mcp="true", skill_path_count=0, **{"plugin.name": "pw"}),
            # Тот же плагин в другой сессии — в списке он остаётся одной строкой.
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
    """Без телеметрии раздела нет вовсе — пустой список читался бы как «серверов нет»."""
    assert "connections" not in digest.build(conn, datetime(2026, 8, 14, tzinfo=UTC))["mcp"]


def test_slash_commands_are_counted(conn: Any) -> None:
    """В транскрипте от слэш-команды остаётся разметка, а не имя: парсер её не
    разбирает, телеметрия называет команду прямо."""
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
    assert len(stats["commands"]) == 2  # промпт без команды в список не попадает

    built = digest.build(conn, datetime(2026, 8, 14, tzinfo=UTC))["off_transcript"]
    assert built["prompts"]["commands"][0]["command"] == "clear"


def test_hook_time_is_counted(conn: Any) -> None:
    """Хук выполняется между ходами: в транскрипте на его месте просто пауза,
    и HTTP-хук к недоступному сервису там неотличим от раздумий модели."""
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
            # Начало выполнения времени ещё не знает — считать надо завершения.
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
    """Хуки регистрируются заново в каждой сессии — считаются уникальные пары."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("hook_registered", 1, hook_event="SessionStart", hook_type="http"),
            event("hook_registered", 2, hook_event="Stop", hook_type="http"),
            event("hook_registered", 3, hook_event="Stop", hook_type="http"),  # вторая сессия
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
    """`start_type` размечает запуски: транскрипт такой разметки не несёт,
    там продолжение видно только по копиям ходов."""
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
    assert counts["telemetry"] == 1  # все точки об одной сессии
    assert counts["transcripts"] == 1
    assert {row["start_type"] for row in counts["starts"]} == {"fresh", "resume", "continue"}


def test_work_done_is_counted_from_metrics(conn: Any) -> None:
    """Строки кода и активное время считает сам Claude Code — в истории их нет."""
    lines = "claude_code.lines_of_code.count"
    active = "claude_code.active_time.total"
    otlp.ingest(conn, "metrics", metrics_payload(point(120, type="added"), name=lines))
    otlp.ingest(conn, "metrics", metrics_payload(point(35, type="removed"), name=lines))
    otlp.ingest(conn, "metrics", metrics_payload(point(90, type="user"), name=active))
    otlp.ingest(conn, "metrics", metrics_payload(point(210, type="cli"), name=active))

    work = metrics.otel_work(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert work["lines_added"] == 120
    assert work["lines_removed"] == 35
    # Активное время — сумма обоих видов: и человек за клавиатурой, и работа CLI.
    assert work["active_seconds"] == 300
    assert work["waiting_seconds"] == 210


def test_api_failures_are_visible_only_here(conn: Any) -> None:
    """Сорвавшийся запрос в транскрипт не попадает: там только итоговый ответ."""
    otlp.ingest(
        conn,
        "logs",
        logs_payload(
            event("api_error", 1, status_code="429", attempt=1),
            event("api_error", 2, status_code="429", attempt=2),
            event("api_error", 3, status_code="529", attempt=1),
            event("api_refusal", 4),
            event("api_request", 5),  # удавшийся запрос ошибкой не считается
        ),
    )
    stats = metrics.otel_errors(conn, datetime(2026, 8, 14, tzinfo=UTC))
    assert stats["errors"] == 4
    assert stats["by_status"][0] == {"status": "429", "errors": 2}
    assert {row["status"] for row in stats["by_status"]} == {"429", "529", "—"}


def test_tool_times_come_from_events(conn: Any) -> None:
    """Время в инструменте есть только в телеметрии: в транскрипте между
    запросом и результатом лежит и ожидание разрешения."""
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
    assert [row["tool"] for row in rows] == ["Bash", "Read"]  # порядок по времени
    assert rows[0]["calls"] == 2
    assert rows[0]["seconds"] == 5.0
    assert rows[0]["slowest"] == 4.032
    assert rows[0]["failures"] == 1
    assert metrics.session_tool_times(conn, "нет-такой") == []


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
    """Сквозная проверка: событие в БД доходит до статуса в списке сессий."""
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
