# Specification: "The Claude speedometer" (working name: cburn)

A local application that watches every Claude Code session on the machine,
shows token spend in real time (a "speedometer" per session and a combined one)
and once an hour analyses the activity, suggesting concrete optimisations: what to move
into a skill, where to fix permissions, which MCP to connect or switch off, where a session
has overgrown and it is time to run `/clear`.

The prototype of the task is a manual report on navuik/core: 62 sessions, an average context
of 330k tokens per turn, 87% of the spend inside two unclosed work lines, idle bridge turns.
All of that must be found automatically rather than by hand once a fortnight.

---

## 1. Decisions taken during the discussion

| Question | Decision                                                                                                                                  |
| -------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Scope    | Every project on the machine, with a project filter in the interface                                                                       |
| Form     | First a plain web application on localhost + reports into the telegram bridge; the desktop wrapper (Tauri) and the macOS menu-bar icon are the final stage |
| Advisor  | `claude -p` on Haiku (by default) or Sonnet, over a digest aggregated beforehand                                                           |

## 2. Data sources

**The main one: Claude Code transcripts.** Claude Code writes a full log of every session
into JSONL: `~/.claude/projects/<project-path-slug>/<session-id>.jsonl`. Every assistant
turn holds a `message.usage` block with the fields `input_tokens`, `output_tokens`,
`cache_creation_input_tokens`, `cache_read_input_tokens` and the model name; the lines carry
`sessionId`, `uuid`/`parentUuid` (resume forks are reconstructed from them), `timestamp`,
`cwd` and the record type (user / assistant / tool_use / tool_result / summary). That is
enough for every dashboard metric, retrospectives over the whole history included.

Important: the transcript format is not officially documented and changes between Claude Code
versions. The parser must be tolerant: ignore unknown fields, stash unknown record types into
`raw_events` and never fail. The `~/.claude` directory is opened strictly read-only.

**The additional one (phase M4): OpenTelemetry.** Claude Code supports an official export
of metrics and events: `CLAUDE_CODE_ENABLE_TELEMETRY=1` + an OTLP endpoint
(`OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`). The application raises its own
OTLP receiver and gets ready metrics of cost, tokens and tool decisions without parsing
files. The channel is in beta at Anthropic, so it complements the transcripts rather than
replacing them. Check the current metric specification against
https://code.claude.com/docs/en/monitoring-usage before implementing M4.

Claude Code hooks are **not used** for collecting data - they are already taken by the
telegram bridge, and a second layer is unnecessary: a file watcher over JSONL gives a
latency below a second.

## 3. Architecture

A Python service (FastAPI) with four subsystems and a shared SQLite database; the interface
is an ordinary web page in a browser. No desktop wrapper until the final stage.

```
~/.claude/projects/**/*.jsonl ──▶ Collector (watchdog + incremental parser)
                                        │
localhost:4317 (OTLP, M4) ─────────────▶│
                                        ▼
                                   SQLite (~/.local/share/cburn/cburn.db)
                                        │
              ┌─────────────────────────┼──────────────────────┐
              ▼                         ▼                      ▼
        API server (FastAPI)       Analyzer (cron)        Notifier (telegram)
        HTTP + WebSocket           claude -p haiku
        127.0.0.1:8799                  │
              │                         └── advice => SQLite => Notifier
              ▼
   Frontend (React + Vite), opened in a browser: http://localhost:8799
   Start: `cburn serve` (one process, uvicorn) or a launchd agent
   Final stage: the same frontend is wrapped into Tauri + a menu-bar tray
```

Why Python rather than Rust/Tauri right away: the start is an order of magnitude faster (the
whole toolchain is already in the project's daily use), UI iterations happen in a browser with
hot reload, and Tauri later wraps the finished web frontend with virtually no changes - it is
only necessary to respect two constraints from the outset: all frontend-to-backend exchange
goes over HTTP/WebSocket on localhost (no direct filesystem access from the frontend) and no
browser APIs that the system webview lacks. Python handles streaming parsing of large
transcripts (67 MB in the prototype project): line-by-line reading with offsets, heavy
aggregates in SQL. If the initial indexing of the whole history turns out slow, it is sped up
pointwise (`orjson`, workers) rather than by a rewrite.

### Collector

Watches `~/.claude/projects/` through a file watcher (`watchdog`, FSEvents on macOS).
For every file it keeps the offset of the last read position - it reads only the tail. It
detects truncation and recreation of files (the offset resets by inode + size). The initial
indexing of the whole history runs as a background job with progress in the UI; live data
already flows meanwhile. A session counts as "live" if the file grew within the last
120 seconds, and as "an active request" if the last record is not a finished assistant turn.

### SQLite: the data model

```sql
files(path PK, inode, size, offset, mtime)
projects(id PK, slug, root_path, display_name)
sessions(id PK, project_id, started_at, last_at, first_prompt,   -- trimmed to 200 chars
         parent_session_id,        -- resume fork
         turns INT, tokens_in, tokens_out, cache_read, cache_write,
         cost_usd, is_live)
turns(id PK, session_id, ts, model, role,
      input_tokens, output_tokens, cache_read, cache_write,
      context_estimate,            -- input + cache_read + cache_write
      cost_usd, is_idle)           -- the idle turn heuristic, see §6
tool_calls(id PK, turn_id, tool,   -- Bash, Edit, Read, Task, mcp__*...
           detail)                 -- for Bash: the normalised command (first word + subcommand)
advice(id PK, ts, period_start, period_end, digest_json,
       response_md, model, cost_usd, max_severity)
model_prices(model PK, in_per_mtok, out_per_mtok,
             cache_write_per_mtok, cache_read_per_mtok)  -- edited in the settings
```

Per-minute and per-hour aggregates are computed on the fly by SQL queries; should performance
degrade, add a materialised `minute_stats` table.

## 4. Metrics and the "speedometers"

The `context_estimate` of the last turn is the "tank level" of a session;
the speedometer needle is the rate of spend.

| Metric               | Definition                                                                                                                             |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Burn rate            | tokens/min and $/hour, sliding windows of 1 / 5 / 60 min; per session and machine-wide                                                  |
| Session context      | the `context_estimate` of the last turn, a scale up to 200k with zones: green < 80k, yellow < 150k, red above                           |
| Cost                 | by the `model_prices` table; separately "what it would cost over the API", even on a subscription rate                                  |
| Model share          | turns and tokens of Opus / Sonnet / Haiku over the period                                                                               |
| Tool profile         | the distribution of calls, and inside Bash by normalised commands                                                                       |
| Idle turns           | turns where the model answer is shorter than 10 tokens on a context > 50k (the "waiting" case from the report)                          |
| Limit window         | an estimate of the approach to the 5-hour and weekly subscription limits from local data; marked as an approximation, refined over OTel in M5 |

## 5. The interface

**The "Overview" screen.** The main speedometer (the combined burn rate and $/hour), today's
counter, a live feed of the latest turns of every session (project, model, token delta),
the top 5 sessions by spend over 24 hours, an indicator of the advisor at work and its own
cost (the self-cost must be visible honestly).

**The "Sessions" screen.** A table of every session with a filter by project and status
(live / idle / finished): the project, the first prompt, the duration, the turns, the context
now, the cost, a spend sparkline. Resume fork chains collapse into one row with an expander.

**The "Session" screen.** A timeline: a chart of `context_estimate` over turns (the moment of
auto-compaction and of a fork is visible), a feed of turns with tools, a cost breakdown by
model, marks on idle turns. A "build the advisor digest right now" button.

**The "Advice" screen.** The history of advisor reports with severity and status
(new / accepted / dismissed - dismissed tips travel into the digest of later runs as "already
dismissed, do not repeat").

**The "Settings" screen.** The config from §8 as a form.

**The menu bar (the Tauri tray, the final stage M5).** An icon with the current combined burn
rate (thousand tokens/min or $/h - switchable). A red dot on any active alert. A drop-down
menu: the three hottest sessions with mini-speedometer needles, the last tip, the
"Open the dashboard" and "Pause notifications for 2 hours" items.

## 6. The advisor (analyzer)

It runs on a schedule (once an hour by default) and only if there was activity over the period
(otherwise it quietly skips the tick). It works in two steps, so that it costs pennies itself.

**Step 1, without an LLM: the digest.** The collector builds a compact JSON (the target is up
to 20k tokens): the period aggregates; the list of sessions with a context above the threshold
and the number of turns after the last "empty" point; fork chains; the top 20 normalised
bash commands with their frequencies and the top repeated patterns (heredoc scripts are
collapsed by shingles); a counter of idle turns with duration samples; the share of Opus on
mechanical operations (the heuristic: turns with Bash/Edit only and no user text); the size of
CLAUDE.md and the `@`-imports for every active project; the list of connected MCP servers and
how often their tools are actually used; the frequency of permission confirmations (from
transcript events). The digest **does not include** conversation text - only commands, paths,
tool names and numbers. The `allow_snippets = true` option permits including up to 200
characters of repeated commands verbatim.

**Step 2: `claude -p`.** A call of the form:

```bash
claude -p --model claude-haiku-4-5 --output-format json \
  --max-turns 1 < digest_prompt.txt
```

A fixed system prompt demands a JSON array of tips in return:
`{category: skill|mcp|permissions|hooks|hygiene|model, severity: info|warn|crit,
title, body_md, evidence: [references to sessions/commands from the digest]}`.
A tip without evidence is dropped. The advisor model is configurable (haiku by
default, sonnet for the weekly deep analysis). The exact model name for
`--model` is to be checked against the current documentation at implementation time.

Examples of tips the system must produce on the data from the prototype report:
"722 repeated python heredocs over `.test-cases/*.json` - a candidate for a CLI script
and a skill", "session X: 477 prompts and 26 forks without a reset - apply the rule
one task = one session", "31,752 turns on Opus for mechanical edits - move them to Sonnet",
"dozens of 'waiting' turns on a 300k context - fix the bridge Stop hook", "MCP server Y is
connected, its tools were never called in 30 days - switch it off".

## 7. Telegram notifications

The default channel is the existing telegram bridge (an HTTP POST to `localhost:8788`;
the exact endpoint contract is an open question in §11, to be clarified before M3). The
fallback channel is the direct Bot API with `bot_token` and `chat_id` in the config.

Three message kinds. The hourly digest goes out only if the advisor returned at least
one tip of warn severity or above - there are no empty "all is well" messages. The daily
summary at a configurable time (21:00 by default): the day's spend, the top 3 sessions,
a comparison with yesterday. Instant alerts, bypassing the advisor schedule: a session
context passed the threshold (150k by default), more than N idle turns in a row (5 by default),
the burn rate stayed above the threshold for longer than 10 minutes. Every alert has a 30-minute
cooldown per session, and the global "pause for 2 hours" from the menu bar applies to
everything except crit.

## 8. Configuration

`~/.config/cburn/config.toml`, editable from the UI as well:

```toml
[watch]
include = ["**"]            # project slugs
exclude = []

[thresholds]
context_warn = 80000
context_crit = 150000
idle_run = 5
burn_rate_warn_per_min = 50000

[analyzer]
enabled = true
interval_minutes = 60
model = "haiku"             # haiku | sonnet
weekly_deep_model = "sonnet"
allow_snippets = false

[telegram]
mode = "bridge"             # bridge | bot | off
bridge_url = "http://localhost:8788/..."
bot_token = ""
chat_id = ""
daily_summary_at = "21:00"

[server]
port = 8799

[prices]                    # $/MTok, edited by hand when the price list changes
# "claude-opus-4-8" = { in = ..., out = ..., cache_write = ..., cache_read = ... }
```

## 9. Non-functional requirements

Privacy: everything is stored and computed locally; exactly two streams go out - the digest
into `claude -p` (that is, into the Anthropic API, where the transcripts went anyway during
work) and the text of telegram notifications. The conversation content never leaves the
machine while `allow_snippets = false`.

Performance: reading the tail of a live file takes <= 200 ms from the watcher event;
idle CPU stays below 1%; the initial indexing of 10 GB of history runs in the background
without blocking the UI.
Reliability: a parser failure on one broken line does not stop the collector
(the line goes to the log, the offset moves on); file deletion and rotation are survived;
the database is restored by a full reindex with the `cburn reindex` command.
Compatibility: macOS comes first, but until M5 the application is an ordinary
web service, and portability comes for free; the frontend must not use browser APIs
missing from the system webview, so that the Tauri wrapper at the final stage goes
through without rewrites.

## 10. Milestones

| Stage | Content                                                                                                                                | Acceptance criterion                                                                                                                                |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| M1    | The CLI prototype: the JSONL parser, SQLite, the commands `cburn stats`, `cburn sessions`, `cburn session <id>`                     | The numbers for one real session match a manual count over the usage fields; a full indexing of the machine's history goes through without failures |
| M2    | The API server + the web dashboard: the Overview, Sessions, Session and Settings screens; live over WebSocket; `cburn serve` + a launchd agent for autostart | The dashboard opened in a browser shows the turn of the current Claude Code session with a latency <= 1 s                                            |
| M3    | The advisor + telegram: the digest, `claude -p`, the Advice screen, all three notification kinds                                        | On a replay of the prototype project's history the advisor finds at least the mega-session, the idle turns and the heredoc pattern; one tick costs <= $0.02 on haiku |
| M4    | The OTLP receiver, refined subscription limits, the weekly deep analysis                                                                | The OTel metrics and the JSONL metrics agree on a shared session within 2%                                                                          |
| M5    | The desktop: a Tauri wrapper around the finished frontend, a tray with the burn rate and a menu, autostart instead of launchd           | The menu-bar icon updates live; the application is one installable .app; the frontend did not change                                                 |

## 11. Risks and open questions

The JSONL transcript format is undocumented and changes between Claude Code versions - hence
a tolerant parser, a set of fixtures from real transcripts of different versions and a
"parse the whole history without failures" smoke test in CI. The estimate of the subscription
limits from local data is approximate - mark it honestly in the UI until M4. Model prices
change - they live in the config, not in the code. The OTel channel is in beta - that is why
it is M4 rather than the foundation.

Open questions before the start: the contract of the telegram bridge on `localhost:8788`
(the endpoint, the format) - needed for M3, not blocking before that; whether aggregation
across several machines will be wanted eventually (in the current specification, no, but the
database schema must not stand in its way: put `machine_id` into advice and sessions in
advance as an empty field).
