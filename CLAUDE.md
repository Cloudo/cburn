# cburn

A local "speedometer" service for Claude Code token spend: it watches every
session on the machine, shows the burn rate in real time and once an hour
suggests optimisations. The full specification is @TZ.md, and it is the source of truth for
requirements; this file holds the working rules on top of it. The decomposition into tasks
and their status live in `.local/ROADMAP.md`; when closing a task, tick it there. That file
is written in Russian and stays out of git - see the rule about `.local/` below.

## Stack and startup

Python 3.11+ (FastAPI + uvicorn, watchdog, orjson, httpx) and SQLite. The frontend is
React + Vite (the `web/` directory). The package manager is pip inside `.venv`,
there is no `uv` on the system.

The same commands are collected in the `Makefile` (`make` prints the list): `make check`
is the three checks in a row, `make web` is the frontend, `make serve` is the dashboard.

```bash
.venv/bin/python -m pytest -q                                  # tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .      # lint and format
.venv/bin/mypy                                                 # types
.venv/bin/cburn paths                                          # check the paths
.venv/bin/cburn reindex && .venv/bin/cburn serve                # indexing and the dashboard
cd web && npm install && npm run build                          # the frontend into web/dist
```

`web/dist` is not committed: after a clone the frontend has to be built, otherwise
`/` serves a hint with the endpoint list instead of the dashboard.

All three checks must pass before a commit.

## Structure

```
src/cburn/
  paths.py          paths (transcripts, config, database) - the single place where they are set
  config.py         ~/.config/cburn/config.toml, defaults per TZ §8
  cli.py            the `cburn` command
  db/schema.sql     the SQLite schema, applied idempotently in connect()
  collector/        watchdog + the incremental JSONL parser (M1)
  api/              FastAPI: HTTP + WebSocket on 127.0.0.1 (M2)
  analyzer/         the digest + `claude -p` (M3)
  collector/otlp.py receiving Claude Code telemetry over OTLP/JSON (M4)
  notifier/         the telegram bridge and the Bot API (M3)
  ui_state.py       the browser's choice mirrored for the tray (the interface language)
  actions.py        carrying a tip out: the act, the diff, the confirmation, the rollback (D7)
tests/fixtures/transcripts/   anonymised transcripts for the parser tests
```

## Invariants (never to be broken)

- **`~/.claude` is read-only apart from one door.** The transcripts
  (`~/.claude/projects`) and `~/.claude.json` are never written to: the first is the data
  source, and a write there moves the very offsets we read the tail from; the second is
  live state Claude Code rewrites every few seconds, so a read-modify-write of ours would
  silently lose someone else's change. What may be written is `settings.json` (the user
  one) and a project's `settings.local.json` - and only through `actions.py`. Everything
  else of ours still lives in `~/.local/share/cburn/`.
- **A tip is carried out through a plan, not through text.** The advisor returns a typed
  `act` from a closed list next to the words; anything the machine does not know is
  dropped and the tip stays text (`actions.normalise`). The endpoint builds the plan and
  gives back a diff of the file plus the hash of its current state; the confirmation comes
  back with that hash, and a mismatch is a 409 rather than an overwrite - Claude Code
  rewrites `settings.json` itself, and a stale plan must not wipe that out.
- **Every write leaves a way back.** A copy goes into `~/.local/share/cburn/backups/` and
  a row into `applied_patches` together with the diff the human confirmed. A rollback
  refuses when the file has changed since we wrote it: restoring the copy would throw
  away someone else's later edit. The whole door is switched off by `actions.enabled`.
- **A session is closed in a pause, not in the middle of a step.** An accepted
  `close_session` lands in `applied_patches` as `pending`, and the liveness pass sends the
  signal when `session_status` is no longer `working`. SIGTERM stays honest about itself:
  `SessionEnd` hooks will not run, and the plan says so before the confirmation.
- **The parser is tolerant.** The transcript format is undocumented and changes between
  versions: ignore unknown fields, stash unknown record types into `raw_events`, and a broken
  line goes to the log without stopping the walk - the offset moves on.
- **Incrementality.** Only the file tail is read from the stored offset; truncation and
  recreation are caught by the inode + size pair. `reindex --full` is therefore run with
  the server stopped: the watcher writes an offset for the file being appended to right
  now, and the walk started next to it reads that file from the end - checked live, the
  prompts of the current session went missing exactly like that.
- **Privacy.** The conversation text never leaves the machine. Only tool names, normalised
  commands, paths and numbers go into the advisor digest; including command fragments happens
  exclusively under the `allow_snippets` flag. The prompt log (`prompts`) is stored locally
  and shown on screen, and the digest knows nothing about it - what a human typed is for
  the human, not for the model.
- **Room for Tauri (M5).** The frontend talks to the backend over HTTP/WebSocket on localhost
  only - no direct filesystem access; no browser APIs missing from the system webview. The
  wrapper in M5 must not require reworking the frontend.
- **The dashboard layout is the browser's business, not the server's.** Positions, sizes and
  hidden widgets live in `localStorage` (`cburn.layout.v3`, the keys of the former project
  name are read as a fallback); the API knows nothing about them. The markup inside a widget
  reacts to its own width through `@container` rather than to the window width: widgets are
  dragged by hand while the window does not change.
- **The colour theme is data, and the choice is the browser's business too.** The eighteen
  palettes live in `web/src/themes.ts` as a table of thirteen tokens - the same ones
  `styles.css` declares in `:root` - and are borrowed from the VS Code themes of the same
  name, so the dashboard offers the choice a person already made in the editor. The chosen
  theme writes those tokens onto `<html>`, and the stylesheet keeps only the fallback palette
  and what depends on the kind alone (the row tint, the shadows). The choice lies in
  `localStorage` next to the layout and the language: the id in `cburn.theme`, and beside it a
  remembered dark and light theme, which is what "follow the system" switches between. A
  colour written into the code instead of a token would stay at the old theme - that is why
  the chart slices read `var(--steel)` rather than a hex.
- **The subscription limits come from Anthropic, we do not count them.** Claude Code calls
  `GET /api/oauth/usage` with an OAuth token from the macOS keychain (the
  `Claude Code-credentials` entry) and stores the answer in `~/.claude.json` under
  `cachedUsageUtilization`. That cache refreshes only when Claude Code itself
  opens `/usage` and lags by days, so the main path is our own request
  (no more often than once every 5 minutes, the endpoint answers 429 with `Retry-After`),
  and the cache is the fallback. The token is never stored: only percentages go out.
- **Model prices live in the config, not in the code** (the `[prices]` section, the
  `model_prices` table). Hardcoding rates is not allowed.
- **`machine_id`** in `sessions` and `advice` is left empty in advance for a possible
  aggregation across several machines - do not remove it.
- **The session-to-process link is `claude agents --json`.** It prints the active sessions
  (interactive ones included) with `pid`, `sessionId`, `cwd` and a name. The process itself
  does not reveal the `sessionId`: it is neither in the arguments nor in the open descriptors -
  the transcript is appended to and closed right away.
- **"Waiting for permission" is recognised by processes, not by the transcript.** A long tool
  and a hanging "allow?" question look identical in the JSONL: a tool request without an
  answer. What tells them apart is a child of the session process started after the request
  (`busy_since` in `sessions`); MCP servers and background commands start earlier and do not
  count. Tools without a process of their own (MCP calls, `WebFetch`) still show up as waiting
  for a permission after 25 s.
  Where telemetry is on, the guess is unnecessary: the decision arrives as a `tool_decision`
  event (milestone E), and there is no reason to wait a quarter of a minute. The process rule
  stays as the fallback - for sessions without telemetry and for the case when it was switched
  off mid-work (`OTEL_STALE_SECONDS` in `metrics.py`).
- **`SessionEnd` hooks do not run on SIGTERM.** Claude Code has no signal handler of its own:
  `SIGINT`/`SIGHUP`/`SIGTERM` cause an immediate `process.exit()`, while hooks run
  asynchronously on a regular exit (`/exit`, Ctrl+D, `/clear`, logout). There is no
  "finish someone else's session" command in the CLI either, so the dashboard sends SIGTERM
  and warns about that honestly in the popover.

## What is already known about the transcript format

Verified against the real history (590 MB, Claude Code versions 2.1.220-228) - these things
diverge from the specification text, and they are what to trust:

- **A record is not a turn**: one assistant answer is spread over several JSONL records
  (one per `thinking`/`text`/`tool_use` block), and each carries the full `usage`. The turn
  key is `message.id`; summing over records inflates the spend roughly fourfold.
- **`usage` in the records of a turn is uneven**: for some records it is still zero - the
  spend is filled in when the answer completes (2,527 turns out of 15,197). The correct value
  is the element-wise maximum over the turn's records: the first record understates the spend
  by a third, the sum inflates it several times over.
- **A file is not a session**: one JSONL holds several `sessionId`s. Turns are grouped by the
  record field, the file name is only a key in `files`.
- **`usage` is wider than the specification**: `cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens`,
  `iterations[]`, `service_tier`, `speed`; next to the record sit `effort` and `requestId`.
  A write into the 1h cache is billed differently from a 5m one - count them apart.
- **There are more than a dozen record types** (`attachment`, `file-history-snapshot`,
  `queue-operation`, `ai-title`, `frame-link`...), and the `summary` type is gone;
  auto-compaction shows as `isCompactSummary: true` on a user record.
- **`user` records come in two kinds**: a real prompt and a `tool_result`. Tell them apart by
  the presence of a `tool_result` block in the content, not by `promptSource` - that one is
  present on less than 5% of records. Prompt content comes both as a string and as an array of
  blocks.
- **Subagents** are marked `isSidechain` and live in the parent session's file.
- **The session title** comes from `ai-title` records (generated) and `custom-title` (set by a
  human, and it wins). They hold only a `sessionId` and the text: neither a time nor a uuid,
  so they do not affect the session state.
- **A slash command in the transcript** is a `<local-command-caveat>` plus
  `<command-name>`/`<command-message>` blocks, with no live text in the record at all.
- **The transcript is appended in bursts every 2-6 s**, and `usage` appears only together with
  a finished turn. The spend inside an unfinished turn is invisible in the JSONL: finer than a
  5-second granularity is impossible without OTel (M4).
- **Turns are duplicated across files**: resume copies past turns into a new file with a new
  `sessionId`, keeping `uuid` and `message.id` (copies of one turn were seen in 20 files).
  Deduplication is mandatory and goes by `message.id`, `uuid` will not do for it.
- **`<synthetic>`** in `message.model` marks service answers ("No response requested", hitting
  the limit) with zero usage and no `requestId`; they do not become turns.

## What is known about `claude -p` (the advisor)

Checked against the installed Claude Code 2.1.231 - the contract changes, so check it again
at the next advisor edit:

- **`--max-turns` is gone.** Extra turns are cut off by an empty `--tools ""`
  (without tools the model has nothing to continue with) and by `--max-budget-usd`.
- **`--json-schema` works in `-p` too**: the parsed answer arrives in a separate
  `structured_output` field, and there is no need to dig JSON out of the text by hand. The
  `result` field is there as well - it holds the same structure as a string.
- **The `--output-format json` envelope** carries `total_cost_usd`, `usage`,
  `modelUsage` (the full model name), `num_turns`, `stop_reason`, `is_error`.
- **The `haiku` alias is alive** and expands into `claude-haiku-4-5-20251001`.
- **The schema may hold an optional nested object**, and the model fills it. Checked on a
  live call with the `act` of task D7: haiku returned `close_session`, `allow_permission`
  (with `scope` and `project`), `disable_plugin` and `disable_hook` for the four sections
  of the digest, and a tip without a fitting action simply came back without `act`.
- **A tick costs about $0.08** on haiku with a digest of ~1.5k tokens: almost all of that is
  writing the system prompt into the cache (11k tokens). `--strict-mcp-config` and
  `--exclude-dynamic-system-prompt-sections` trim it but do not zero it out.
  The "<= $0.02 per tick" threshold from TZ §10 is unreachable on today's CLI.

## What is known about OTel telemetry (milestone E)

Checked against https://code.claude.com/docs/en/monitoring-usage and against live runs of
Claude Code 2.1.222 on 14 August 2026:

- **We take `http/json`, not gRPC.** The encoding is parsed by the standard json module, so
  neither `grpcio` nor `opentelemetry-proto` is needed among the dependencies, and the
  receiver lives right inside `cburn serve` without occupying a second port:
  `OTEL_EXPORTER_OTLP_ENDPOINT` points at `http://127.0.0.1:8799/otlp`, and Claude Code
  appends `/v1/metrics` and `/v1/logs` itself. Port 4317 from TZ §3 is gRPC, and we do not
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

## Notifications (milestone D)

- **An alert does no counting of its own.** The thresholds and the numbers come from the same
  overview the screen shows: otherwise the phone and the dashboard would carry different
  numbers, and neither would be trusted.
- **The memory lives in the database, not in the process.** `notifications` stores what went
  out and when: the cooldown survives a restart, and a failed send is marked
  `ok = 0` - otherwise silence after a failure would look like "we already warned".
- **A pause does not switch the instrument off.** Two hours of silence hold everything except
  `crit`: when the spend is burning right now, staying quiet is not an option. It is set by a
  tray item and by the `/api/notify/pause` endpoint.
- **The bridge token is not duplicated.** It is read from `~/.config/cc-tg-bridge/config.json`,
  that is, from where the bridge itself reads it.
- **The notification tick lives inside the advisor loop** - it already ticks once a minute,
  and a second loop for two checks is unnecessary.

## The desktop (milestone F)

- **One copy of the instrument, and the guard is doubled.** Two trays over one server show
  one number twice, and two `cburn serve` read the transcripts twice and send the telegram
  alert twice. The window is held by `tauri-plugin-single-instance` (registered first of
  all the plugins): a second launch calls `tray::show_dashboard` in the first one and exits.
  The server is held by a `flock` on `~/.local/share/cburn/serve.lock`
  (`instance.only_one`) - the port does not do it, `--port 8800` would start a second
  watcher happily. The lock dies with the process, so a crash leaves nothing stale behind.

- **The window loads the page from the server, not from files.** The frontend calls the API
  with relative paths (`api/overview`); from `tauri://` they would go nowhere,
  so `frontendDist` points at `http://127.0.0.1:8799`. Thanks to that the
  frontend did not change by a single line for M5 - the acceptance criterion is met literally.
- **The tray counts nothing itself:** every five seconds it takes `/api/overview`, and the
  alert threshold comes from `/api/config`, so that it obeys the same number that is edited
  in "Settings". The polling lives in its own thread: the menu bar lives without the window.
- **What the tray shows is the tray's own business, like the dashboard layout.** The figures
  of the icon title are ticked in the "menu bar shows" submenu (burn rate, $/h, the day, the
  percentages of the 5-hour and weekly windows), and the choice lies in
  `~/.local/share/cburn/tray.json` - the API knows nothing about it, exactly as it knows
  nothing about the widget positions. Keys are stored there rather than a bit mask: the file
  is read by a human and survives a change in the order of `METRICS`.
- **The tray speaks the language of the dashboard, and the language is still the browser's.**
  It lives in `localStorage` like the layout, and the tray cannot read that: on every switch
  the frontend mirrors the choice through `POST /api/ui/lang`, the server writes
  `~/.local/share/cburn/ui.json` (an atomic rename - the tray reads it whenever it likes),
  and the poll relabels the menu without a restart. The mirror is one-way, and the server
  takes no language of its own from it: texts still never come from the server. Until the
  dashboard has been opened once the menu is English - the tray has no `navigator.language`.
- **The `.app` raises the server when it stays quiet** (`CBURN_SERVE`, then the
  usual install locations). The interpreter is not packed inside the `.app` -
  a deliberate limitation: the Python part is installed separately, as before.
- **The `dmg` is not built:** `bundle_dmg.sh` fails on this machine, and an image is not
  needed for "one installable `.app`". Only `app` is left in `bundle.targets`.
- **Rust was installed with `--no-modify-path`** - the toolchain lives in `~/.cargo`, the
  shell profile was left alone; the build is called as
  `PATH=$HOME/.cargo/bin:$PATH npm run desktop:build`.
- **In dev mode the window is fed by vite, in the build by the server.** `devUrl` points
  at `http://localhost:5173` and `beforeDevCommand` starts vite, so `make desktop` gives
  the same hot reload inside the window as in a browser; the API and the WebSocket are
  proxied by vite onto the running `cburn serve`, which still has to be up. The port is
  fixed with `strictPort`: `tauri dev` waits for exactly that address, and a silent hop to
  5174 would leave it waiting forever. `frontendDist` is left pointing at the server - the
  built `.app` loads the page from `http://127.0.0.1:8799` as before.
- **The desktop build runs from the repository root, not from `web/`.** Tauri looks for
  `src-tauri/` next to itself, while `web/` holds only the frontend - from there the command
  fails with "Couldn't recognize the current folder as a Tauri project".

## Decisions taken

They live in `DECISIONS.md` in the repository root - the option taken, the ones rejected
and the reason. A new decision is written down there, not here: this file keeps the rules
and the invariants.

## Conventions

- Heavy aggregates are computed in SQL, not in Python.
- The normalised bash command is the only thing kept from a Bash call: no arguments, no paths,
  no file names. The subcommand is taken only for commands from the allowlist in `parser.py`,
  otherwise a file name leaks into the database disguised as a subcommand.
- Claude Code hooks are not used for collecting data - only the file watcher.
- Commits follow Conventional Commits (the `conventional-commits` skill), with messages in
  English.
- **Russian lives in the interface dictionaries and nowhere else:** `web/src/dict.ts` for
  the dashboard and `src-tauri/dict.json` for the tray menu. Both are the same device - a
  key and a pair of languages, the reader picks one - and they are data, not code: the pairs
  are baked into the binary by `include_str!`, so the menu needs no build step of its own.
  Everything else - the code, the comments, the CLI output, the HTTP answers, the telegram
  notifications and the documentation - is English. The advisor prompt is English too, and
  the language of its answers is a setting (`analyzer.language`), not a hardcoded phrase.
- **`.local/` is the Russian half, and it never reaches git.** Working documents the author
  reads rather than ships - the roadmap first of all - live there in Russian; the directory is
  in `.gitignore`. That is what keeps the two rules from colliding: the repository stays
  English, and the plan is still readable in the native language. A file asked for in Russian
  goes into `.local/`, never next to the code.
- **Texts the frontend shows never come from the server.** The server sends data (a limit
  window `kind`, a dictionary key for a failed request), and the words around it are built
  by `dict.ts`: the interface has two languages, and the backend has none.
- External facts that change (the OTel metric specification, model names for
  `--model`, the telegram bridge contract) are checked against the documentation at
  implementation time rather than taken from memory.
- **A test that looks at a "today" slice is not nailed to a date.** A payload with a
  hardcoded `2026-08-14` fell inside the daily window yesterday and no longer does today -
  three telemetry tests failed exactly at the turn of the day. Parsing is checked with a
  fixed time (the format, deduplication, window bounds), while everything that counts "today"
  and "over the last day" is built from `datetime.now`.

## Milestones

| Stage | Content | Status |
| ---- | ---------- | ------ |
| M1 | The CLI: the JSONL parser, SQLite, `stats`/`sessions`/`session`, `reindex` | in progress |
| M2 | FastAPI + the web dashboard, live over WebSocket, `cburn serve`, launchd | - |
| M3 | The advisor (`claude -p`) and telegram notifications | done |
| M4 | The OTLP receiver, refined subscription limits, the weekly analysis | done |
| M5 | The Tauri wrapper and the menu-bar tray | done |

The acceptance criteria for every stage are in TZ §10.
