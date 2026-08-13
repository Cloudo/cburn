"""Тесты советчика (задача D2).

Настоящий `claude -p` не зовётся: он стоит денег и ходит в сеть. Вместо него
подставляется свой раннер, отдающий конверт того же вида, что и CLI на самом
деле — форма конверта сверена с установленной версией Claude Code.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cloudo_dash.analyzer import advisor
from cloudo_dash.db import connect

DIGEST = {"period": {"since": "2026-08-13T00:00:00+00:00", "until": "2026-08-13T12:00:00+00:00"}}


def envelope(advice: list[dict], cost: float = 0.021) -> dict:
    """Конверт `--output-format json` в том виде, в каком его отдаёт CLI."""
    return {
        "is_error": False,
        "num_turns": 2,
        "stop_reason": "tool_use",
        "total_cost_usd": cost,
        "result": json.dumps({"advice": advice}, ensure_ascii=False),
        "structured_output": {"advice": advice},
        "modelUsage": {"claude-haiku-4-5-20251001": {"costUSD": cost}},
    }


def runner_for(payload: dict) -> object:
    def run(prompt: str, model: str, budget_usd: float) -> dict:
        run.prompt = prompt  # type: ignore[attr-defined]
        return payload

    return run


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "advice.db")


def test_command_matches_installed_cli(conn: sqlite3.Connection) -> None:
    """Флаги — те, что есть в установленной версии; `--max-turns` в ней нет."""
    command = advisor.build_command("haiku", budget_usd=0.05)

    assert command[:4] == ["claude", "-p", "--output-format", "json"]
    assert "--max-turns" not in command, "флага больше нет, ходы ограничивают пустые --tools"
    assert command[command.index("--model") + 1] == "haiku"
    assert command[command.index("--max-budget-usd") + 1] == "0.05"
    # Инструменты выключены: советчик смотрит на готовый дайджест.
    assert command[command.index("--tools") + 1] == ""
    schema = json.loads(command[command.index("--json-schema") + 1])
    assert schema["properties"]["advice"]["items"]["required"] == [
        "title",
        "severity",
        "detail",
        "action",
        "evidence",
    ]


def test_advice_without_evidence_is_dropped(conn: sqlite3.Connection) -> None:
    """Совет без опоры на цифры — общие слова, такие не сохраняем (ТЗ §6)."""
    payload = envelope(
        [
            {
                "title": "Следите за контекстом",
                "severity": "warn",
                "detail": "",
                "action": "",
                "evidence": "",
            },
            {
                "title": "Перевести чтение файлов на haiku",
                "severity": "warn",
                "detail": "212 ходов только с Read и Grep",
                "action": "поставить haiku на механические ходы",
                "evidence": "mechanical_opus.turns = 212, opus_cost_usd = 59.3",
            },
        ]
    )

    result = advisor.advise(conn, DIGEST, runner=runner_for(payload))

    assert [item["title"] for item in result["advice"]] == ["Перевести чтение файлов на haiku"]
    rows = conn.execute("SELECT title, evidence, status FROM advice_items").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "new"


def test_run_is_recorded_with_its_own_cost(conn: sqlite3.Connection) -> None:
    """Собственная стоимость такта видна честно — она пишется в advice."""
    payload = envelope(
        [
            {
                "title": "Закрыть разросшуюся линию работы",
                "severity": "crit",
                "detail": "19 сессий в одной цепочке",
                "action": "начать новую сессию",
                "evidence": "chains[0].sessions = 19, cost_usd = 244",
            }
        ],
        cost=0.0234,
    )

    result = advisor.advise(conn, DIGEST, runner=runner_for(payload))

    row = conn.execute("SELECT * FROM advice").fetchone()
    assert row["cost_usd"] == pytest.approx(0.0234)
    assert row["max_severity"] == "crit"
    assert row["model"] == "claude-haiku-4-5-20251001", "в историю пишем полное имя, не алиас"
    assert json.loads(row["digest_json"])["period"]["since"] == DIGEST["period"]["since"]
    assert result["advice_id"] == row["id"]


def test_rejected_advice_goes_into_the_prompt(conn: sqlite3.Connection) -> None:
    """Отклонённый совет уезжает в следующий такт пометкой «не повторять»."""
    first = runner_for(
        envelope(
            [
                {
                    "title": "Отключить playwright",
                    "severity": "info",
                    "detail": "",
                    "action": "убрать сервер",
                    "evidence": "mcp.servers[0].calls = 176",
                }
            ]
        )
    )
    advisor.advise(conn, DIGEST, runner=first)
    with conn:
        conn.execute("UPDATE advice_items SET status = 'rejected'")

    second = runner_for(envelope([]))
    advisor.advise(conn, DIGEST, runner=second)

    prompt = json.loads(second.prompt)  # type: ignore[attr-defined]
    assert prompt["already_rejected"] == [
        advisor.advice_key("Отключить playwright", "убрать сервер")
    ]


def test_broken_answer_does_not_break_the_tick(conn: sqlite3.Connection) -> None:
    """Ответ не по схеме — это ноль советов, а не падение такта."""
    payload = {"is_error": False, "total_cost_usd": 0.01, "result": "я не смог"}

    result = advisor.advise(conn, DIGEST, runner=runner_for(payload))

    assert result["advice"] == []
    assert conn.execute("SELECT COUNT(*) FROM advice").fetchone()[0] == 1, "такт всё равно записан"


def test_error_envelope_is_raised(conn: sqlite3.Connection) -> None:
    """Ошибку CLI прячем в исключение, а не в пустой список советов."""

    def run(prompt: str, model: str, budget_usd: float) -> dict:
        return {"is_error": True, "result": "credit balance too low"}

    with pytest.raises(RuntimeError, match="credit balance"):
        advisor.advise(conn, DIGEST, runner=run)
