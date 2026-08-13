"""Советчик: дайджест → `claude -p` → массив советов (задача D2, ТЗ §6).

Контракт CLI сверен с установленной версией Claude Code (2.1.231), а не взят
по памяти — он меняется между версиями:

* `--max-turns` из плана больше нет; лишние ходы отсекает `--tools ""`
  (без инструментов модели нечем продолжать) и `--max-budget-usd`;
* `--json-schema` заставляет ответ соответствовать схеме, а разобранный ответ
  приходит в поле `structured_output` — руками JSON из текста доставать не надо;
* конверт `--output-format json` несёт `total_cost_usd`, и это честная
  собственная стоимость такта, её и пишем в `advice.cost_usd`;
* `--strict-mcp-config` и `--exclude-dynamic-system-prompt-sections` убирают из
  системного промпта MCP-серверы и сведения о машине: советчику они не нужны,
  а платим за них в каждом такте.

Промпт уходит через stdin: дайджест великоват для аргумента командной строки.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import subprocess
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

CLAUDE_BINARY = "claude"

#: Такт не должен длиться дольше — иначе он налезет на следующий.
TIMEOUT = 300.0

#: Потолок расхода на один такт. Своя стоимость должна быть видна и ограничена:
#: советчик, который дороже своих советов, бессмысленен.
MAX_BUDGET_USD = 0.10

SEVERITIES = ("info", "warn", "crit")

#: Схема ответа. `evidence` обязательна: совет без опоры на цифры дайджеста —
#: это общие слова, такие мы выбрасываем (ТЗ §6).
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "advice": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "detail": {"type": "string"},
                    "action": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["title", "severity", "detail", "action", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["advice"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """
Ты разбираешь расход токенов Claude Code на одной машине и предлагаешь, что
поменять в работе с ним. На входе — JSON-дайджест за период: агрегаты, тяжёлые
сессии, линии работы, профиль инструментов, доля моделей.

Правила:
1. Каждый совет опирается на конкретные числа из дайджеста. В `evidence` —
   эти числа и откуда они взяты. Без опоры совет не нужен: лучше меньше.
2. Никаких общих мест вида «следите за контекстом» и «используйте кэш».
   Совет должен говорить, что именно сделать: вынести в скилл, отключить
   MCP-сервер, сменить модель на конкретной работе, закрыть разросшуюся линию.
3. `severity`: crit — расход уже сгорает прямо сейчас; warn — заметная утечка;
   info — стоит иметь в виду.
4. Не больше пяти советов. Не повторяй то, что помечено уже отклонённым.
5. Отвечай по-русски, без вводных и без пересказа дайджеста.
""".strip()


def build_command(model: str, budget_usd: float = MAX_BUDGET_USD) -> list[str]:
    """Собрать команду запуска. Вынесено ради тестов: сеть в них не ходит."""
    return [
        CLAUDE_BINARY,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(RESPONSE_SCHEMA, ensure_ascii=False),
        "--system-prompt",
        SYSTEM_PROMPT,
        "--model",
        model,
        # Инструменты советчику не нужны: он смотрит на готовый дайджест.
        "--tools",
        "",
        "--strict-mcp-config",
        "--exclude-dynamic-system-prompt-sections",
        "--max-budget-usd",
        str(budget_usd),
    ]


def run_claude(prompt: str, model: str, budget_usd: float = MAX_BUDGET_USD) -> dict[str, Any]:
    """Позвать `claude -p` и вернуть разобранный конверт ответа."""
    result = subprocess.run(  # noqa: S603 — команда собрана здесь же
        build_command(model, budget_usd),
        input=prompt,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude -p вернул {result.returncode}: {result.stderr.strip()[:400]}")
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ответ claude -p не разобран: {result.stdout[:200]}") from exc
    return dict(envelope)


def parse_advice(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Достать советы из конверта и выбросить те, что не на чём не держатся."""
    payload = envelope.get("structured_output")
    if not isinstance(payload, dict):
        # Запасной путь: схема не сработала, но текст ответа обычно всё равно JSON.
        try:
            payload = json.loads(envelope.get("result") or "{}")
        except json.JSONDecodeError:
            log.warning("советчик ответил не по схеме, советов нет")
            return []
    items = payload.get("advice") if isinstance(payload, dict) else None
    advice = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        evidence = (item.get("evidence") or "").strip()
        if not title or not evidence:
            log.info("совет без опоры на цифры выброшен: %s", title or "без заголовка")
            continue
        severity = item.get("severity") if item.get("severity") in SEVERITIES else "info"
        advice.append(
            {
                "key": advice_key(title, item.get("action")),
                "title": title,
                "severity": severity,
                "detail": (item.get("detail") or "").strip(),
                "action": (item.get("action") or "").strip(),
                "evidence": evidence,
            }
        )
    return advice


def advice_key(title: str, action: str | None) -> str:
    """Отпечаток совета: по нему отклонённый совет узнаётся в следующем такте."""
    seed = f"{title.strip().lower()}|{(action or '').strip().lower()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def rejected_keys(conn: sqlite3.Connection) -> list[str]:
    """Что человек уже отклонил — советчику это повторять незачем."""
    return [
        row["key"]
        for row in conn.execute("SELECT DISTINCT key FROM advice_items WHERE status = 'rejected'")
    ]


def advise(
    conn: sqlite3.Connection,
    digest: dict[str, Any],
    *,
    model: str = "haiku",
    budget_usd: float = MAX_BUDGET_USD,
    runner: Any = None,
    kind: str = "manual",
) -> dict[str, Any]:
    """Прогнать дайджест через модель и записать советы. Возвращает итог такта.

    `kind` — как такт случился: `manual` из CLI и с кнопки, `hourly`/`weekly`
    от планировщика. По нему считается расписание и подписывается разбор.
    """
    call = runner or run_claude
    prompt = json.dumps(
        {"digest": digest, "already_rejected": rejected_keys(conn)},
        ensure_ascii=False,
    )
    envelope = call(prompt, model, budget_usd)
    # Проверка живёт здесь, а не в раннере: ошибку должен ловить любой источник
    # конверта, в том числе подменённый в тестах.
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p сообщил об ошибке: {envelope.get('result')}")
    advice = parse_advice(envelope)
    period = digest.get("period") or {}
    cost = float(envelope.get("total_cost_usd") or 0.0)
    severity = _max_severity(advice)

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO advice (ts, kind, period_start, period_end, digest_json, response_md,
                                model, cost_usd, max_severity, status)
            VALUES (:ts, :kind, :start, :end, :digest, :response, :model, :cost, :severity, 'new')
            """,
            {
                "ts": datetime.now(UTC).isoformat(),
                "kind": kind,
                "start": period.get("since"),
                "end": period.get("until"),
                "digest": json.dumps(digest, ensure_ascii=False),
                "response": envelope.get("result"),
                "model": _model_name(envelope, model),
                "cost": cost,
                "severity": severity,
            },
        )
        advice_id = int(cursor.lastrowid or 0)
        conn.executemany(
            """
            INSERT OR IGNORE INTO advice_items
                (advice_id, key, title, severity, detail, action, evidence)
            VALUES (:advice_id, :key, :title, :severity, :detail, :action, :evidence)
            """,
            [dict(item, advice_id=advice_id) for item in advice],
        )
    return {
        "advice_id": advice_id,
        "advice": advice,
        "cost_usd": cost,
        "max_severity": severity,
        "model": _model_name(envelope, model),
    }


def _max_severity(advice: list[dict[str, Any]]) -> str:
    order = {name: index for index, name in enumerate(SEVERITIES)}
    return max((item["severity"] for item in advice), key=lambda name: order[name], default="info")


def _model_name(envelope: dict[str, Any], fallback: str) -> str:
    """Полное имя модели из конверта: алиас `haiku` в истории ничего не скажет."""
    usage = envelope.get("modelUsage")
    if isinstance(usage, dict) and usage:
        return next(iter(usage))
    return fallback
