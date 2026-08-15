"""The advisor: digest -> `claude -p` -> an array of tips (task D2, TZ §6).

The CLI contract was checked against the installed Claude Code (2.1.231) rather than
taken from memory - it changes between versions:

* the planned `--max-turns` is gone; extra turns are cut off by `--tools ""`
  (without tools the model has nothing to continue with) and `--max-budget-usd`;
* `--json-schema` forces the answer to match a schema, and the parsed answer
  arrives in the `structured_output` field - no need to dig JSON out of text by hand;
* the `--output-format json` envelope carries `total_cost_usd`, and that is the honest
  cost of the tick itself, which is what goes into `advice.cost_usd`;
* `--strict-mcp-config` and `--exclude-dynamic-system-prompt-sections` strip MCP servers
  and machine details out of the system prompt: the advisor does not need them,
  yet we pay for them on every tick.

The prompt goes through stdin: the digest is a bit large for a command-line argument.
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

#: A tick must not run longer than this - otherwise it would overlap the next one.
TIMEOUT = 300.0

#: The spend ceiling for one tick. Our own cost must be visible and bounded:
#: an advisor that costs more than its advice is pointless.
MAX_BUDGET_USD = 0.10

SEVERITIES = ("info", "warn", "crit")

#: The answer schema. `evidence` is mandatory: a tip without support from the digest
#: numbers is just general words, and those we throw away (TZ §6).
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

Две секции приходят из телеметрии самого Claude Code, а не из транскриптов:
`permissions` — сколько раз работа вставала ради ручного подтверждения, по
каким инструментам и как часто человек уходил в другой режим разрешений
(повод предложить правку `permissions.allow`), и
`off_transcript` — служебные запросы, которых в транскриптах нет вовсе, то
есть остальные цифры дайджеста на эту величину занижены; там же активное
время работы и строки кода: расход сам по себе ни хорош, ни плох — важно,
что за него сделано. Если у секции `available: false`, телеметрия выключена
— молчи про них, а не считай нулём. Оттуда же `mcp.connections`: сколько
секунд уходит на подключение каждого MCP-сервера при запуске сессии. Сервер,
который стартует секунды и ни разу не позван, — повод его отключить. И
`off_transcript.hooks`: сколько времени съели хуки и какие вообще объявлены.
Хук выполняется между ходами, поэтому его ожидание выглядит как пауза, а не
как расход, — но человек ждёт ровно так же.

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
    """Assemble the command. Extracted for tests: they never touch the network."""
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
        # The advisor needs no tools: it looks at a ready digest.
        "--tools",
        "",
        "--strict-mcp-config",
        "--exclude-dynamic-system-prompt-sections",
        "--max-budget-usd",
        str(budget_usd),
    ]


def run_claude(prompt: str, model: str, budget_usd: float = MAX_BUDGET_USD) -> dict[str, Any]:
    """Call `claude -p` and return the parsed answer envelope."""
    result = subprocess.run(  # noqa: S603 - the command is assembled right here
        build_command(model, budget_usd),
        input=prompt,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude -p returned {result.returncode}: {result.stderr.strip()[:400]}")
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"the claude -p answer was not parsed: {result.stdout[:200]}") from exc
    return dict(envelope)


def parse_advice(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the tips out of the envelope and drop the ones resting on nothing."""
    payload = envelope.get("structured_output")
    if not isinstance(payload, dict):
        # Fallback: the schema did not fire, but the answer text is usually JSON anyway.
        try:
            payload = json.loads(envelope.get("result") or "{}")
        except json.JSONDecodeError:
            log.warning("the advisor answered off-schema, no tips")
            return []
    items = payload.get("advice") if isinstance(payload, dict) else None
    advice = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        evidence = (item.get("evidence") or "").strip()
        if not title or not evidence:
            log.info("a tip without support in numbers was dropped: %s", title or "untitled")
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
    """A tip fingerprint: it lets a dismissed tip be recognised on the next tick."""
    seed = f"{title.strip().lower()}|{(action or '').strip().lower()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def rejected_keys(conn: sqlite3.Connection) -> list[str]:
    """What the human has already dismissed - no point in the advisor repeating it."""
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
    """Run the digest through the model and store the tips. Returns the tick result.

    `kind` is how the tick happened: `manual` from the CLI and from the button,
    `hourly`/`weekly` from the scheduler. It drives the schedule and labels the analysis.
    """
    call = runner or run_claude
    prompt = json.dumps(
        {"digest": digest, "already_rejected": rejected_keys(conn)},
        ensure_ascii=False,
    )
    envelope = call(prompt, model, budget_usd)
    # The check lives here and not in the runner: the error must be caught for any
    # envelope source, including one swapped in tests.
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p reported an error: {envelope.get('result')}")
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
    """The full model name from the envelope: the `haiku` alias tells nothing in history."""
    usage = envelope.get("modelUsage")
    if isinstance(usage, dict) and usage:
        return next(iter(usage))
    return fallback
