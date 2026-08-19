---
paths:
  - "src/cburn/collector/otlp.py"
  - "src/cburn/metrics.py"
---

# OTel telemetry (milestone E)

Checked against https://code.claude.com/docs/en/monitoring-usage and against live runs of
Claude Code 2.1.222 on 14 August 2026:

- **We take `http/json`, not gRPC.** The encoding is parsed by the standard json module, so
  neither `grpcio` nor `opentelemetry-proto` is needed among the dependencies, and the
  receiver lives right inside `cburn serve` without occupying a second port:
  `OTEL_EXPORTER_OTLP_ENDPOINT` points at `http://127.0.0.1:8799/otlp`, and Claude Code
  appends `/v1/metrics` and `/v1/logs` itself. Port 4317 from SPEC §3 is gRPC, and we do not
  tie ourselves to it.
- **The environment variables are printed, not written.** Telemetry is switched on only by
  Claude Code's own environment, and `~/.claude` is read-only for us, so
  `cburn otel` shows ready lines (`--env`, `--settings`) and a human pastes them in. The
  client reads the environment at start - a restart is needed.
- **Metrics arrive as delta counters**, an unacknowledged payload may be repeated, so every
  point gets a fingerprint (name + window + attributes): without it a retry would double the
  numbers.
- **`query_source` separates the main work from the service one.** In the metrics it is
  coarsened to `main` / `auxiliary` / `subagent`, while in `api_request` events it is exact
  (`sdk`, `generate_session_title`). On `main` telemetry and the JSONL agree
  **exactly** (0% on two sessions, task E3).
- **Service requests never reach the transcript at all.** Generating a session title is a
  separate haiku call (~535 tokens, $0.0006 per session), and all that remains of it in the
  JSONL is an `ai-title` record without usage. That is, the transcript-based dashboard
  understates the spend, and seeing this is possible only through OTel.
- **Events give what the transcript does not:** `tool_decision` with the permission decision
  and its source (`user_permanent`, `config`, `hook`) - the very frequency of permission
  confirmations the digest lacked (D1); `tool_result` with `duration_ms`; `api_request` with
  the exact price and duration; `mcp_server_connection` with the server connection time and
  its outcome.
- **`duration_ms` on `mcp_server_connection` means different things.** With
  `status: connected` it is the connection time (1.7-2.2 s per plugin), with
  `disconnected` it is how long the connection lived (tens of seconds). Adding them
  together is not allowed: the figure inflates tenfold and more.
- **Keys with a dot in the name** (`plugin.name`, `session.id`) require quotes in
  `json_extract`: the path `$.plugin.name` reads as a nested object and quietly yields NULL,
  the correct form is `$."plugin.name"`.
- **There are more events than the documentation describes:** live runs also bring
  `hook_registered`, `hook_execution_start`, `hook_execution_complete` with the
  list of hooks. Parsing is tolerant - an unknown event simply lands in the table.
- **An ununderstood payload is visible in the counters.** The receiver reads only `http/json`;
  with `http/protobuf` the payloads arrive and there is nothing to parse them with. Such a
  payload is acknowledged (retries are pointless) but lands in `dropped`, and
  `cburn otel` shows "payloads N, records 0" instead of "there were no payloads".
- **A busy database is a reason to ask for a retry, not to acknowledge silently.** SQLite has
  one writer, the watcher, the advisor and the cleanup write nearby, and a full
  reindex holds the transaction longer than the connection is ready to wait.
  The receiver answers 503, and the exporter repeats the payload itself; a 200 in that place
  would lose the data.
- **The telemetry slice in the overview is cached for 5 seconds.** A measurement over 100,000
  events (`tools/otel_bench.py`): a day's slice costs 68 ms against 4 ms for all the rest of
  the overview - the most expensive part is parsing the JSON attributes of `api_request`
  and `tool_decision`, of which there are tens of thousands a day. The overview goes
  to subscribers every second, and the exporter sends payloads every 5-10 seconds,
  so there is nothing to recompute more often.
- **Telemetry has its own retention.** Events arrive several per turn and per
  tool call, around 400 bytes each (37 events for a one-minute session): without a limit the
  tables outgrow the useful data. The cleanup runs at server start and once a day, the
  retention is `otel.keep_days` (30 days by default, 0 keeps everything). It does not touch
  the parser data.
- **Personal attributes and the content of the work are not stored.** Every point carries
  `user.email`, `user.account_id`, `organization.id` and other things identical for the
  machine. And `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES`,
  `OTEL_LOG_TOOL_DETAILS` and `OTEL_LOG_RAW_API_BODIES` replace `<REDACTED>`
  with the real texts of prompts, answers and tool arguments. A human is free to switch them
  on for their own debugging, but the dashboard must not become a conversation store because
  of it: both are dropped during parsing (`SKIPPED_ATTRS` in `collector/otlp.py`), and only
  lengths and counters remain.
- **`start_type` on `session.count`** marks the start: `fresh`, `resume`,
  `continue`, `agents_view`. In the transcript a continuation is visible only through copied
  turns, so the marking is useful in itself, and next to the parser's session count
  (`cburn otel`) it works as a reconciliation of two channels.
- **Hooks eat noticeable time, and that is visible only here.** In the transcript a hook is
  just a pause - model thinking cannot be told from waiting on an HTTP hook there. On my own
  66-second session `UserPromptSubmit` took 15.9 s and `Stop` took 34.5 s (both go to the
  telegram bridge), while `PreToolUse` and `PostToolUse` fitted into 6-7 ms. The duration
  comes from `hook_execution_complete` (`total_duration_ms`); the start event does not have it
  yet.
- **A network refusal and a client failure are counted apart.** After an `api_error` with a
  429 the request is repeated and the work goes on, while an `internal_error` breaks it off
  midway: the tokens spent are not coming back. Mixing them into one counter would hide the
  second trouble behind the first.
- **`plugin_loaded` tells what a plugin drags along:** `has_mcp`,
  `has_hooks`, `skill_path_count`, `command_path_count`. The plugin itself is free, while its
  MCP server takes seconds to connect in every session - the chain "plugin => server =>
  seconds of startup" is visible only through telemetry.
- **Slash commands are visible only here.** In the transcript a command leaves
  `<command-name>` blocks instead of live text, and the parser does not unpack them; the
  `user_prompt` event names the command directly (`command_name`, `command_source`).
- **Where this data is used:** the "past the transcripts" widget on the dashboard
  (service spend, manual confirmations, permission mode switches, failed API requests,
  active time and lines of code), the time inside tools on the session screen, the
  "past the history" and "permissions" lines in `cburn stats`, the `off_transcript`
  and `permissions` sections of the advisor digest and deciding the "waiting for permission"
  status. Everywhere telemetry is a refinement on top of the transcripts:
  when it is absent, everything is counted the old way, and the digest sections are marked
  `available: false`, so that the advisor does not take missing data for a zero.
