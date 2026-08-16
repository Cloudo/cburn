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

from .. import actions

log = logging.getLogger(__name__)

CLAUDE_BINARY = "claude"

#: A tick must not run longer than this - otherwise it would overlap the next one.
TIMEOUT = 300.0

#: The spend ceiling for one tick. Our own cost must be visible and bounded:
#: an advisor that costs more than its advice is pointless.
MAX_BUDGET_USD = 0.10

SEVERITIES = ("info", "warn", "crit")

#: The answer schema. `evidence` is mandatory: a tip without support from the digest
#: numbers is just general words, and those we throw away (TZ §6). `act` is optional and
#: comes from a closed list (task D7): most tips have no action that could be carried out.
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
                    "act": actions.ACT_SCHEMA,
                },
                "required": ["title", "severity", "detail", "action", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["advice"],
    "additionalProperties": False,
}

#: In which language the tips come back. The prompt is English, the answer follows the
#: `analyzer.language` config key: the tips are read by a human, and that is their choice.
LANGUAGE_NAMES = {"en": "English", "ru": "Russian"}

SYSTEM_PROMPT_TEMPLATE = """
You analyse the Claude Code token spend on a single machine and suggest what to
change in the way it is used. The input is a JSON digest for a period: aggregates, heavy
sessions, work lines, the tool profile, the model share.

Two sections come from Claude Code's own telemetry rather than from the transcripts:
`permissions` - how many times the work stopped for a manual confirmation, for
which tools and how often the human went into another permission mode
(a reason to suggest a `permissions.allow` fix), and
`off_transcript` - service requests that are absent from the transcripts entirely, which
means the other digest numbers are understated by that much; the same section holds the
active working time and the lines of code: the spend is neither good nor bad on its own -
what matters is what was done for it. If a section has `available: false`, telemetry is
switched off - stay silent about it rather than treating it as zero. From there also comes
`mcp.connections`: how many seconds connecting every MCP server takes at session start. A
server that takes seconds to start and was never called is a reason to switch it off. And
`off_transcript.hooks`: how much time the hooks ate and which ones are declared at all.
A hook runs between turns, so waiting for it looks like a pause rather than
spend - but the human waits exactly the same.

Rules:
1. Every tip rests on concrete numbers from the digest. `evidence` holds
   those numbers and where they come from. Without support a tip is not needed: fewer is better.
2. No generalities like "watch your context" or "use the cache".
   A tip must say what exactly to do: move something into a skill, switch off an
   MCP server, change the model for a particular kind of work, close an overgrown line.
3. `severity`: crit - the spend is burning right now; warn - a noticeable leak;
   info - worth keeping in mind.
4. No more than five tips. Do not repeat what is marked as already dismissed.
5. Answer in {language}, without preambles and without retelling the digest.
6. If a tip can be carried out by one of the actions below, fill `act`. Nothing runs by
   itself: the human sees the diff of the file and confirms it, and the change can be
   rolled back. Where no action fits, leave `act` out rather than inventing one.
   * `close_session` {{session_id}} - a session whose context has grown too heavy. It is
     terminated in a pause between steps, not in the middle of one.
   * `allow_permission` {{rule, scope, project}} - a rule for `permissions.allow` when the
     same tool is confirmed by hand over and over. `rule` is written in the Claude Code
     syntax, for example `Bash(npm test:*)` or `Read(//tmp/**)`. `scope` is `user` (the
     default) or `project`, and then `project` holds the project name from the digest.
   * `disable_hook` {{event, matcher}} - a hook that eats time between turns. `event` is
     the name from the digest (`Stop`, `UserPromptSubmit`); without `matcher` the whole
     event goes.
   * `disable_plugin` {{plugin}} - a plugin whose MCP server takes seconds at every
     session start and is never called.
""".strip()


def system_prompt(language: str = "en") -> str:
    """The system prompt with the answer language substituted (`analyzer.language`)."""
    return SYSTEM_PROMPT_TEMPLATE.format(language=LANGUAGE_NAMES.get(language, "English"))


def build_command(
    model: str, budget_usd: float = MAX_BUDGET_USD, language: str = "en"
) -> list[str]:
    """Assemble the command. Extracted for tests: they never touch the network."""
    return [
        CLAUDE_BINARY,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(RESPONSE_SCHEMA, ensure_ascii=False),
        "--system-prompt",
        system_prompt(language),
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


def run_claude(
    prompt: str, model: str, budget_usd: float = MAX_BUDGET_USD, language: str = "en"
) -> dict[str, Any]:
    """Call `claude -p` and return the parsed answer envelope."""
    result = subprocess.run(  # noqa: S603 - the command is assembled right here
        build_command(model, budget_usd, language),
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
        # An act the machine does not know is dropped, and the tip stays as text: it is
        # carried out on confirmation, so what gets through is only what we can undo.
        act = actions.normalise(item.get("act"))
        advice.append(
            {
                "key": advice_key(title, item.get("action")),
                "title": title,
                "severity": severity,
                "detail": (item.get("detail") or "").strip(),
                "action": (item.get("action") or "").strip(),
                "evidence": evidence,
                "act": act,
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
    language: str = "en",
) -> dict[str, Any]:
    """Run the digest through the model and store the tips. Returns the tick result.

    `kind` is how the tick happened: `manual` from the CLI and from the button,
    `hourly`/`weekly` from the scheduler. It drives the schedule and labels the analysis.
    `language` is the language of the answer (`analyzer.language`).
    """
    call = runner or run_claude
    prompt = json.dumps(
        {"digest": digest, "already_rejected": rejected_keys(conn)},
        ensure_ascii=False,
    )
    envelope = call(prompt, model, budget_usd, language)
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
                (advice_id, key, title, severity, detail, action, evidence, act_json)
            VALUES (:advice_id, :key, :title, :severity, :detail, :action, :evidence, :act_json)
            """,
            [
                dict(
                    item,
                    advice_id=advice_id,
                    act_json=json.dumps(item["act"], ensure_ascii=False) if item["act"] else None,
                )
                for item in advice
            ],
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
