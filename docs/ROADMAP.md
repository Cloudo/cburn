# The cburn implementation plan

A decomposition of the [specification](../TZ.md) into tasks. Ticks are placed as work is
done; decisions fixed along the way move into [CLAUDE.md](../CLAUDE.md).

The order of work is a vertical slice: first a thin end-to-end path
"parser => SQLite => API => a dashboard with a needle", then depth on M1 and M2.
Checking early that the numbers add up and the needle is alive matters more than a complete
CLI.

## What every stage gives

In plain words, without terms: what you get from every task. The summary as of
14 August 2026 - **the plan is complete: 36 tasks out of 36**, every milestone is closed.

### Milestone A - a live needle ✅

| Task | What it gives |
| ------ | ------------ |
| A0. The plan | It is visible what is done and in which order; priorities can be argued before the code rather than after |
| A1. The parser | The application understands what Claude Code wrote down. Without it there is not a single number |
| A2. Import into the database | The history lands in the database and is not re-read on every start |
| A3. Accuracy of the numbers | The numbers can be trusted: they match a manual count to zero |
| A4. The watcher | New turns appear on the screen by themselves, nothing has to be pressed |
| A5. The API | The data reaches the screen. The desktop application later grows out of this same joint |
| A6. The frontend | There is a page with a needle: the spend is seen by eye rather than in tables |
| A7. Live readings | The needle lives during a pause too: the spend honestly falls instead of freezing at the last turn |
| A8. Live sessions | It is visible what every session is waiting for right now, and a superfluous one can be closed from the screen |

### Milestone B - completeness and scale ✅

| Task | What it gives |
| ------ | ------------ |
| B1. Cost | It is visible what the work would have cost at API rates. A subscription is not paid that way, but it is the only common scale: it weighs input, output and both cache writes against each other |
| B2. History indexing | The whole history is read in seconds - there is nothing to wait for even on the first start |
| B3. Metrics | Where the tokens go: models, tools, idle turns, the approach to the limit |
| B4. Liveness | Finished sessions do not get in the way of watching the current ones: out of 28 "idle" ones only 4 were alive |
| B5. Forks and subagents | Work continued after `/clear` is visible as one line rather than as fragments. Subagent spend is visible separately |
| B6. Unknown records | After a Claude Code update it is visible *what* changed in the format, rather than just a dip in the numbers |
| B7. The CLI | The same analysis in the terminal, with project and period filters - without a browser |
| B8. The history smoke test | A Claude Code update will not break the application silently |

### Milestone C - the whole dashboard ✅

| Task | What it gives |
| ------ | ------------ |
| C1. The "Sessions" screen | The history of every session with filters. Right now only the top 5 and the live ones are visible |
| C2. The "Session" screen | It is visible *where* a session bloated and when it is time to run `/clear` |
| C3. The "Settings" screen | Prices, thresholds and telegram are edited in a window rather than in a config file |
| C4. The advisor cost | It is visible what the advisor itself costs - so that it does not eat more than it saves |
| C5. Autostart | The dashboard starts by itself and survives a reboot |

### Milestone D - the advisor and telegram ✅

| Task | What it gives |
| ------ | ------------ |
| D1. The digest | A summary of the period: where the spend is, where the idle turns are. The conversation does not leave the machine in the process |
| D2. The model call | The advice itself: what to move into a skill, where to fix permissions, which MCP to switch off |
| D3. The scheduler | The advice arrives by itself once an hour rather than when you remember to look |
| D4. The bridge channel | The technical joint with the telegram bot (an edit in the neighbouring project) |
| D5. Notifications | Telegram receives the hourly digest, an alert on a spend spike and the daily summary |
| D6. The "Advice" screen | Tips are not lost, and dismissed ones do not come round again |
| D7. Acceptance | A check on the real history: the advisor finds the same things you found by hand |

### Milestone E - OpenTelemetry ✅

| Task | What it gives |
| ------ | ------------ |
| E1-E3 | The numbers come from Claude Code itself rather than being reconstructed from files: the spend absent from the transcripts (service requests) becomes visible, along with how many times you confirmed permissions and how many seconds every tool ran |
| E4 | That data reaches the places where it is looked at: the dashboard widget, the lines in `cburn stats`, the advisor digest sections and an honest "waiting for permission" status instead of a guess by processes |

### Milestone F - the desktop ✅

| Task | What it gives |
| ------ | ------------ |
| F1-F3 | An ordinary application instead of a browser tab, and the spend needle in the menu bar, always in sight |

**Milestone D is what all of this was started for.** Everything before it builds instruments;
the advisor is the only one that says what to change.

## Reconnaissance: facts that change the implementation

A survey of `~/.claude/projects` on the development machine (590 MB, 347 transcripts,
19 projects, Claude Code versions 2.1.220-228):

- **A file is not a session.** The largest transcript (64 MB) holds two different
  `sessionId`s. Turns have to be grouped by the record field, while the file name is used
  only as a key in `files`.
- **A record is not a turn** (checked over 172,447 records). One assistant answer is spread
  over several JSONL records - one per content block (`thinking`, `text`,
  every `tool_use`), and each carries the *full and identical* `usage`. Over the whole
  history 70,061 assistant records give 15,151 turns (x4.6). The turn key is `message.id`
  (equivalent to `requestId`); summing usage over records doubles the spend.
- **`usage` inside a turn is uneven:** for 2,527 turns out of 15,197 some records carry
  zeros - the spend is filled in when the answer completes. What has to be taken is the
  element-wise maximum over the turn's records: by the first record the sum comes out as
  8.1M output tokens instead of 12.8M, and by the sum of records it is inflated manyfold.
- **Turns are duplicated across files** - contrary to the initial measurement. On resume
  Claude Code copies past turns into a new file with a new `sessionId`, keeping
  `uuid` and `message.id`: one turn was met in 20 files. Unique `uuid`s number
  28,525 out of 70,061 records, unique (`sessionId`, `message.id`) pairs number 38,199 out of
  15,151 turns. Deduplication is mandatory, and the key is `message.id`, not `uuid`.
- **`usage` is richer than in the specification:** `cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens`,
  `iterations[]`, `service_tier`, `speed`, and next to the record sit `effort` and `requestId`.
  The real values on the machine are exactly `ephemeral_1h` (an example: 963k cache read,
  596 written into the 1h cache).
- **The cache rates differ:** a write into the 1h cache costs more than into the 5m one. One
  `cache_write_per_mtok` column in `model_prices` is not enough - a separate one is needed
  for 1h.
- **There are far more record types than in the specification:** `assistant`, `user`,
  `attachment`, `system`, `mode`, `last-prompt`, `custom-title`, `ai-title`,
  `queue-operation`, `file-history-snapshot`, `file-history-delta`, `permission-mode`,
  `frame-link`, `agent-name`. There is no `summary` type in current versions; auto-compaction
  shows as `isCompactSummary: true` on a user record.
- **Synthetic answers:** `message.model == "<synthetic>"` (120 records) - messages
  like "No response requested" and hitting the session limit. Their `usage` is zero and
  `requestId` is absent; they do not affect the spend but must not become turns.
- **`user` records come in two kinds:** a real prompt and a tool result. They cannot be told
  apart by `promptSource` - the field is present on only 1,929 records out of 45,282 (`sdk`,
  `typed`), and hand-typed prompts usually lack it. The reliable sign of a result is a
  `tool_result` block in the content (41,653 records). Prompt content comes both as a string
  and as an array of blocks (`text`, `image`, `document`).
- **Subagents:** the `isSidechain` field is present; the turns of Task agents live in the same
  file, and their spend must be both added into the session and shown separately.
- **The `cc-tg-bridge` bridge** listens on 127.0.0.1:8788 (the port lives in
  `~/.config/cc-tg-bridge/config.json`), with only the `GET /health` and `POST /hook*` routes
  guarded by `hookToken`. There is no endpoint for arbitrary messages - it is added by task D4.

## Milestone A - the vertical slice (a live needle)

The goal: a page opened in a browser shows the combined burn rate and the turn of the current
Claude Code session with a latency <= 1 s. The metric depth is minimal.

- [x] **A0. The plan into the repository.** This file and a link to it in CLAUDE.md.
- [x] **A1. The transcript line parser.** `src/cburn/collector/parser.py`: the pure
  function `parse_line(raw: str) -> ParsedRecord | None` with no database access.
  It parses `assistant` (usage, the model, `stop_reason`, `tool_use` blocks), `user`
  (a prompt versus a `tool_result`, `isCompactSummary`), and returns the rest as an
  unknown type. No exceptions escape: a broken line becomes a log line and `None`.
  Done: `RecordKind` (assistant/prompt/tool_result/unknown), `Usage` with
  separate `cache_write_5m`/`cache_write_1h`, bash command normalisation.
  Fixtures of versions 2.1.220-228 are sliced by `tools/make_fixtures.py`
  (anonymisation: they hold no conversation text, arguments or paths).
  A run over the whole history: 172,447 lines, zero unreadable, 4.1 s.
- [x] **A2. Import into the database.** `collector/indexer.py`: `ingest_file(conn, path)` -
  reading from the stored offset line by line, upserting `projects`/`sessions`, inserting
  `turns` and `tool_calls`, moving the offset in `files`. The offset resets on an inode change
  or a size decrease. It is done when a repeated call on the same file changes not a single
  row in `turns`.
  Done: the schema moved to `turns.message_id UNIQUE`, with
  `cache_write_5m`/`cache_write_1h`, `is_sidechain`, `tool_calls.tool_use_id UNIQUE`
  and `sessions.last_context` added. A turn is assembled from several records, and usage is
  merged by an element-wise maximum (including through an UPSERT when the tail is read later).
  A run over the history: 348 files, 172,787 lines, 4.3 s, 15,153 turns, 23,020 copies
  from resume swallowed; a repeated pass reads zero lines.
- [x] **A3. Accuracy of the numbers (the M1 acceptance criterion).** `cburn session <id>`
  prints the totals for one real session; they are checked against an independent `jq` count
  over the same file. The difference is zero. A test pins the reconciliation on a fixture.
  Done: `metrics.py` (the session queries), the commands `reindex`, `sessions`,
  `session <id>` (it accepts an id prefix). The reconciliation on a snapshot of a live session
  (2.7 MB, 209 turns): the turns, the input, the output, the cache reads and both cache writes
  matched to the token. The reconciliation is pinned by a test over every fixture.
  It has to be done on a snapshot: on a live file the difference comes not from an error
  but from the transcript being appended between `reindex` and `jq`.
- [x] **A4. The watcher.** `collector/watcher.py`: `watchdog` over `~/.claude/projects`,
  a debounce of ~200 ms, a file queue, reading only the tail. The check: an `echo`
  into a test JSONL inside a temporary directory leads to a turn appearing in the database.
  Done: `TranscriptWatcher` with a worker thread (the SQLite connection lives
  inside it), a queue with a debounce, an initial walk and an `on_ingest` callback
  for the WebSocket push. The measured latency "line written => turn in the database":
  222-253 ms with a 200 ms debounce. A live check on the real directory:
  the activity of a neighbouring session was caught.
- [x] **A5. The minimal API.** `api/server.py`: FastAPI on 127.0.0.1:8799,
  `GET /api/overview` (the combined burn rate, today's spend, the top sessions),
  `GET /api/sessions`, `WS /ws` with a push on new turns. Plus `cburn serve`
  (uvicorn, starting the watcher in the same process).
  Done: `GET /api/sessions/{id}` and `GET /api/health` as well; the overview metrics
  live in `metrics.py` (the burn rate over 1/5/60 min windows, today's spend from local
  midnight, live sessions, the top sessions). A push on a live session arrives in 0.1 s.
  A note for A6 and B1: the burn rate in tokens is dominated by cache reads
  (2.4M tokens/min against 2.5k of output) - the speedometer needs either
  a breakdown or money, otherwise the scale is meaningless.
- [x] **A6. The minimal frontend.** `web/` - Vite + React + TypeScript, one page:
  the combined burn rate speedometer, today's counter, a feed of the latest turns.
  The exchange with the backend goes over HTTP/WebSocket only. The build lands in `web/dist`,
  and FastAPI serves it as statics.
  Done: the instrument scale is logarithmic (1k ... 10M tokens/min - a linear one is
  useless with a spread of three orders of magnitude), the ring under it is split into shares
  of the parts, there is a separate linear scale for the model output, a switch of the
  1/5/60 min windows, the "today", "running now" and "the most today" panels
  and the turn feed. There are no external fonts and no CDN - the service works offline and
  will move into a webview in M5.

- [x] **A7. Live readings.** The overview stopped depending on watcher events alone:
  a ticker sends it to subscribers every 2 s, so the burn rate windows slide and
  the needle honestly falls during pauses. A chart recorder was added (the spend by 5-second
  buckets over 5 minutes, the bar height following the model output: in total
  tokens every turn looks the same because of the cache) and a "request running" sign
  (`sessions.last_record_kind`: a prompt or a tool_result without an answer).
  The tick is once a second (building the overview costs 2 ms, there is nothing to hit).
  There are four windows now: a 10-second one was added, showing what
  happens right now; the default is still a minute - between turns a
  short window honestly shows zero.
  The needle moves smoothly: the rotation goes through a CSS `transform` rather than
  recomputed line coordinates - a browser does not animate a transition on the
  `x1/y1/x2/y2` attributes, and the needle jumped. The figure under the scale follows it along
  the same curve.
  The accuracy limit was measured: Claude Code appends the transcript in bursts every
  2-6 s, and `usage` appears only together with a finished turn - the spend
  inside an unfinished turn is invisible in the transcript in principle. That needs
  the OTLP receiver (M4).

- [x] **A8. Working with live sessions.** The session title from `ai-title` and
  `custom-title` records (a self-set one beats a generated one), the age and the last activity
  time on the card, the list limited to five sessions and sorted by
  activity. Closing a session: a confirmation in a popover, then a SIGTERM to the
  process and a `sessions.hidden` mark; a separate "just remove" button
  does not touch the process. The process is found by the working directory - the only
  link to the `sessionId`; if more than one process answers for a directory, the dashboard
  terminates nothing.
  Slash command prompts no longer look like a wall of service text:
  the caption becomes the command itself (`/clear`).

**The milestone criterion is met.** `cburn serve`, `http://localhost:8799` open,
a Claude Code session running nearby - the needle moves within a second of a
turn: the "line written => turn in the database" latency is 222-253 ms, and the WebSocket push
takes another 0.1 s. Checked on live sessions, the numbers matched `jq` to the token.

Discovered along the way: the share of cache reads in the spend is 99%, so the combined burn
rate in tokens barely reacts to the model working. The breakdown on the ring and the
separate output scale show that, but money will be the meaningful unit
(task B1).

## Milestone B - completing M1 (completeness and scale)

- [x] **B1. Cost.** The `model_prices` table gains a
  `cache_write_1h_per_mtok` column (an edit to `db/schema.sql`, the database is recreated -
  there are no migrations yet). The `cost_usd` calculation per turn by the model and the four
  parts; the prices come from the `[prices]` config section, with no defaults in the code. The
  `src/cburn/pricing.py` module.
  Done: `pricing.py` moves `[prices]` into `model_prices` and computes the cost
  with a single SQL query - at import time for new turns, whole at server start
  and on the `cburn prices` command. A model without a price costs zero and lands in the
  "no price" list, and a model with a date in its name is billed by the name without the date.
  There are no rates in the code: `cburn prices --init` puts a template
  (`prices.sample.toml`, a price snapshot as of 2026-08-13) into the user config,
  and a human edits the prices afterwards.
- [x] **B2. The initial indexing of the whole history.** A background job with progress
  (`cburn reindex`, the status in the API): a walk over 590 MB without failures, batched
  inserts, `PRAGMA synchronous=NORMAL` for the duration of the import. The timing is recorded
  in the README; if it takes longer than a few minutes, `orjson` and a worker pool follow,
  without a rewrite.
  Settled by measurement: 639 MB, 459 files, 185,799 lines, 17,186 turns - 5.4 s
  from scratch, and a repeated pass takes tenths of a second. With numbers like these a
  background job, a status in the API, batches and a worker pool would optimise what is not
  visible: `ingest_tree` reports progress through a callback, and `cburn reindex` draws a
  walk line and prints the time. `PRAGMA journal_mode=WAL` is already in the schema,
  and under WAL `synchronous` is NORMAL by default anyway.
- [x] **B3. The TZ §4 metrics.** The SQL layer `src/cburn/metrics.py`: the burn rate over
  1/5/60 min windows, the `context_estimate` of the last turn, the model share, the tool
  profile (inside Bash by the normalised command: the first word +
  the subcommand), idle turns (an answer < 10 tokens on a context > 50k), an estimate of the
  limit window marked as "an approximation".
  Done: `model_share`, `tool_profile`, `idle_turns`, `limit_window` and the
  "where the turns go" panel on the dashboard.
  The estimate of the limit window from transcripts turned out to be unnecessary: a source of
  real numbers was found - `GET /api/oauth/usage`, the very one `/usage` in Claude
  Code takes its percentages from. `limits.py` asks it directly with a token from
  the keychain, and the Claude Code cache stays as the fallback. The approximation
  (`limit_window`) remained in metrics as a fallback computation, but the dashboard shows the
  Anthropic percentages. The endpoint is sensitive to frequency: a 429 with
  `Retry-After`, so a request goes no more often than once every 5 minutes with an increasing
  pause.
  Bash command normalisation was reworked from the real data: `cd` was the most frequent
  "command" (2,291 calls), and a variable assignment turned into a directory name
  taken from its own value. Now `cd`/`pushd` are skipped together with their path argument,
  `sudo`/`env`/`time` are unwrapped, and a subcommand is taken only for commands from the
  allowlist - otherwise `cat README` settled in the database as "cat README", that is, as a
  file name against the privacy requirement.
- [x] **B4. Liveness and an active request.** A session is live if the file grew within 120 s;
  a request is active if the last record is not a finished assistant turn. The
  `is_live` flag is refreshed by a background job.
  Done differently from the plan: file growth answers the question "is anything being written
  here", but not "is the session alive" - the transcript does not know the process
  closed, and a finished session hung in "running now" for a whole hour
  (in a real run, out of 28 "idle" ones only 4 were alive). So `is_live`
  is set from `claude agents --json`: a background job in the API polls it every
  15 s (the poll costs ~1.3 s and goes into a thread) and spreads the flag over the sessions.
  A `done` status appeared - "finished", as a tab of its own on the dashboard.
  Three states instead of two: NULL means "not asked", and that is no reason to declare
  a session dead; a silent `claude` also leaves the flags as they were.
  A missing process counts only after 120 s of silence, so that a live
  session does not blink between polls.
  The same pass solves the second task - "waiting for permission" versus "running a
  long tool". They are indistinguishable in the transcript (a tool request without an
  answer), so we look at processes: a session executing a command has a
  child started after the request, while on an "allow?" question the process
  idles. Permanent children (MCP servers) and background commands started
  before the request and do not count - hence `busy_since`, the start moment of the
  youngest child, rather than a "there are children" flag. The poll became more frequent
  (5 s instead of 15): busyness changes with every command, while the expensive
  `claude agents --json` is cached and only `ps` goes out every time.
- [x] **B5. Forks and subagents.** Reconstructing resume chains from
  `parentUuid`/`leafUuid` and from several `sessionId`s inside a file; filling in
  `parent_session_id`. Subagent spend (`isSidechain`) is counted into the session
  and marked separately.
  The link is found not by `parentUuid` but by the copied turns: resume copies
  the history into a new `sessionId`, keeping `message.id`, and deduplication leaves
  such a turn with its first owner - which means the turns of a file recorded against someone
  else's session are exactly the copied history. The direction is set by the start time, not
  by the file walk order: otherwise the link would depend on whose file came
  first. Several `sessionId`s inside one file do not occur in the current data
  (checked over 460 files) - the format version changed since the reconnaissance.
  In the real history 77 links were found, and the longest line is 19 sessions,
  915 turns, $244. `cburn session` prints "continues" and the line total,
  `/api/sessions/{id}` returns `chain`, and `cburn reindex --full` rebuilds the
  links over already read history.
- [x] **B6. Unknown records.** Stashing into `raw_events` with a limit: the full
  payload only for the first N samples of every (type, version) pair, and a counter
  beyond that. Otherwise the table grows faster than the useful data (`attachment` alone
  runs into tens of thousands in history).
  Done: N = 5, the counters live in `raw_event_counts` (the type, the version, how many, when
  first and last seen). In the real history there were 56,685 unknown records
  across 26 pairs - 116 samples were kept instead of 57 thousand, and the database did not
  grow. The worry was confirmed with a margin: `attachment` of version 2.1.222 numbers 33,779.
  To look at them - `cburn events` (what occurs) and `cburn events --show <type>`
  (what it looks like). `cburn reindex --full` clears the counters, otherwise they double.
  The `version` column was added to existing databases through an `ALTER TABLE`:
  a minimal `ADDED_COLUMNS` list appeared in `db/__init__.py`.
- [x] **B7. The whole CLI.** `cburn stats`, `sessions`, `session <id>`, `reindex`
  with project and period filters.
  Done: `cburn stats` prints the turns, the four parts of the spend, the cost,
  the model shares, the tool profile and the idle turns. The shared filters are `--project`
  (a slug substring: `cburn` is enough instead of the full path) and `--period`
  (`today`, `24h`, `7d`, `30d`, `all` or a date). For `sessions` the default period is
  `all` - the list is bounded by `-n` anyway; for `stats` it is `7d`.
  `reindex --project` narrows the walk down to one directory. Model and MCP tool names in the
  output are shortened, as on the dashboard.
- [x] **B8. A smoke test over the whole history.** A test that parses the real
  `~/.claude/projects` whole and fails only on a parser exception;
  it is marked and does not run by default. A set of anonymised fixtures of
  versions 2.1.220-228 lives in `tests/fixtures/transcripts/`.
  Done: `tests/test_real_history.py` under the `real_history` marker,
  switched off through `addopts` and run by hand
  (`pytest -m real_history -q -s`). Two tests: parsing every line without
  exceptions and a full walk into a clean database with a referential integrity check.
  Both print a census - after a Claude Code update it is visible what changed:
  189,170 lines, 74,733 turn records => 17,787 turns, 57,799 unknown,
  253 sessions, 77 resume links, 9.8 s. The fixtures were extended up to 2.1.231:
  versions that did not exist at reconnaissance time appeared in the history.

## Milestone C - completing M2 (the whole dashboard)

- [x] **C1. The "Sessions" screen** - a table with a project and status filter, a spend
  sparkline, fork chains collapsed into a row with an expander.
  Done: the screen is chosen by the hash (`#/sessions`) - a router for two pages is
  unnecessary, but the address survives a reload. `/api/sessions` accepts `project`,
  `status`, `period`, `limit` and returns the projects for the dropdown along with the list.
  The status follows the same rule as on "Overview", but the filtering happens in Python:
  it is derived from several fields, and moving that into SQL would duplicate the
  rule. The sparkline is 24 bars over equal slices of the session's life, in one query for
  the whole list. The list is pulled by a separate request every 5 s rather than over the
  WebSocket: the overview flies to every subscriber once a second, and the screen has no need
  for that.
- [x] **C2. The "Session" screen** - a chart of `context_estimate` over turns with visible
  moments of auto-compaction and forking, a feed of turns with tools, a breakdown
  by model, marks on idle turns.
  Done: `#/session/<id>`. The X axis is the turn number, not the time: pauses between turns
  run into hours, and by time the chart degenerates into a shelf. The 80k/150k zones from
  TZ §4 are painted right on the chart. Auto-compactions are collected at import time into a
  new `session_events` table (a record with `isCompactSummary`), and the branch points come
  from `parent_session_id` - a fork is not a record in the transcript but a link between
  sessions. An idle turn is computed in the query by the same threshold as in the summary
  rather than stored: changing the threshold must not require a reindex.
  For the milestones to appear over already read history, `cburn reindex --full` is needed.
- [x] **C3. The "Settings" screen** - a form over the §8 config, writing through `config.save`,
  editing the model prices.
  Done: `#/settings`, `GET /api/config` and `PUT /api/config`. The value validation
  lives on the backend (`config.validate`) and arrives from there as text: repeating the
  rules on the frontend would mean a second copy of them. Prices apply at once -
  recomputing the whole history takes seconds, and there is no point in waiting for a
  `reindex`.
  Uncovered along the way: `config.load/save` took the path from an argument default, that is,
  as of import time, and it could not be swapped - a test wrote the settings
  into the real user config. Now the path is taken at call time,
  as in `db.connect`.
- [x] **C4. The indexing progress and the advisor self-cost** in "Overview".
  The advisor cost is shown: the sum of today's ticks, their number and their share of the
  daily spend - an instrument that costs more than it saves must
  be visible. While there were no analyses, the row is absent entirely.
  The indexing progress is not done, and that is a decision rather than an omission: the
  measurement in B2 gave 5.4 seconds over 639 MB of history, that is, there is nothing to show -
  the "longer than a few minutes" threshold the progress was meant for is not even close
  (see the README, "How long the first indexing takes").
- [x] **C5. Autostart** - the launchd agent `com.cloudo.cburn.plist`, the commands
  `cburn install` / `cburn uninstall`, the logs in `~/.local/share/cburn/`.
  Done, plus `cburn status`. A user agent, not a system daemon: the dashboard needs no root
  and is harmed by it. What starts is `python -m cburn` rather than the console
  script `cburn`: the interpreter path is known exactly, while a `cburn` on PATH may
  come from another environment. `KeepAlive` only on a failed exit - otherwise
  launchd would raise the dashboard after every manual stop. The tests never call the real
  `launchctl`: the runner is swapped, and the commands and the plist contents are checked.

## Milestone D - M3, the advisor and telegram

- [x] **D1. The digest without an LLM** (`analyzer/digest.py`): the period aggregates, sessions
  above the context threshold, fork chains, the top 20 normalised bash commands,
  collapsing repeated heredocs by shingles, idle turns, the share of Opus on
  mechanical operations, the size of CLAUDE.md and the `@`-imports, the list of MCP servers
  and how often they are actually used, the frequency of permission confirmations.
  The target is up to 20k tokens, with no conversation text. The check: the JSON was built over
  the prototype project's history and fitted into the limit.
  Built, `cburn digest`. Over a week of the real history it was 1,665 tokens out of
  20,000, fitting with a twelvefold margin. Two points were done differently:
  *heredoc shingles* are impossible without storing the command text, and there is none per
  TZ §7 - instead a heredoc is marked right at normalisation time (`python3 <<`), so
  that a repeated run of one script shows in a counter while the text is still not
  stored; *the frequency of permission confirmations* never reaches the transcript
  at all - Claude Code writes neither the permission request nor the answer to it, and
  there is no honest source for that figure before OTel (milestone E). The `@`-imports of
  CLAUDE.md are not expanded yet: only the size of the file itself is counted.
- [x] **D2. The `claude -p` call** (`analyzer/advisor.py`): `--model` (haiku by
  default), `--output-format json`, `--max-turns 1`, a fixed system
  prompt, parsing the array of tips, dropping tips without evidence, accounting for
  its own cost in `advice.cost_usd`. The model name is checked against the current
  documentation at implementation time.
  The contract was checked against the installed version (2.1.231) and diverged from the plan:
  `--max-turns` is gone - turns are bounded by an empty `--tools ""` and
  `--max-budget-usd`; on the other hand `--json-schema` appeared, and the parsed answer
  arrives in the `structured_output` field, so there is no need to parse JSON out of the text.
  The details are in CLAUDE.md. Tips without `evidence` are thrown away, and dismissed ones
  travel into the next tick's prompt as a fingerprint (`advice_items.key`).
  On the real history a tick cost **$0.08** rather than the "<= $0.02" from TZ §10: almost all
  of that is writing the Claude Code system prompt into the cache (11k tokens), and there is
  nothing left to trim it with. The D7 acceptance threshold has to be revised.
- [x] **D3. The scheduler** - a tick every `interval_minutes`, skipping a tick without
  activity, a weekly deep analysis on sonnet.
  It lives in `cburn serve` next to the watcher: the "call the model or not" decision lives
  in the pure `plan_tick`, which is visible in tests. The interval counts from **any** tick
  rather than only from an hourly one: the weekly analysis has just looked at the same data,
  and repeating it for $0.08 is pointless. For the first five minutes after a server start
  there are no ticks - a restart must not cost money by itself. The schedule counts from the
  previous tick rather than by the calendar, so restarts do not shift it.
  The API tests now fail on an attempt to call the real `claude -p`.
- [x] **D4. The `/notify` endpoint in cc-tg-bridge** - a separate task in the neighbouring
  repository: `POST /notify` with `hookToken`, the body `{text, severity, silent}`,
  sending into the MAIN topic; edits in `src/http/server.ts` and `src/http/router.ts`,
  and a note in the bridge README.
  Done in the `feat/notify-endpoint` branch of the neighbouring repository: `POST /notify`
  with the same `hookToken`, the body `{text, severity, silent}`, and the message goes into
  the group's main topic - it has no session, nobody holds a turn, nobody waits for an
  answer. `info` is silent by default. Four tests in the bridge suite (reception,
  401, 400 without text, garbage instead of JSON), and the README was extended.
  Scouted through telemetry (milestone E) before starting: the bridge holds Claude Code hooks
  for 16-35 seconds. For `Stop` and `PermissionRequest` that is by design -
  `createPending` waits for an answer from Telegram up to `waitTimeoutSec` (540 s), and that
  is the point. But `UserPromptSubmit` waits for nothing: there `ensureSession` creates a
  topic and edits a live message, that is, Claude Code stands still while the bridge goes to
  the Bot API. That path is worth making background - the hook can answer `{}` right away.
  For comparison: `PreToolUse` and `PostToolUse` fit into 6-7 ms.
- [x] **D5. The notifier** (`notifier/`): three message kinds (the hourly digest only
  at severity >= warn, the daily summary at 21:00, instant alerts), a cooldown of
  30 min per session, a global pause for 2 hours except crit. The channels: bridge
  (the default), bot, off.
  Done: the rules live as pure functions (`notifier/rules.py`) and are
  checked without a network, the channel (`notifier/channel.py`) knows the bridge, the direct
  Bot API and `off`, and the memory of what went out and when lives in the database - the
  cooldown survives a restart, and a failed send is marked as failed.
  The alert runs no arithmetic of its own: it takes the numbers and thresholds from the same
  overview the screen shows. The notification tick lives inside the advisor loop -
  it ticks once a minute anyway. The pause is set by a tray item and by the
  `/api/notify/pause` endpoint, and `crit` passes through it.
  The bridge token is read from its own config: duplicating a secret in
  two places is not allowed.
  Scouted in advance: on the bridge side everything is ready for `/notify` - the server
  already parses a POST with a Bearer token (`authorized()` in `src/http/server.ts`),
  and the new path is added as a branch next to `/hook` and `/health`. On the dashboard
  side the data is enough too: the thresholds (`thresholds.burn_rate_warn_per_min`,
  `context_crit`) and the channels already live in the config, the tip severity is in
  `advice.max_severity`, and the reasons for instant alerts are computed by `overview`.
  It is logical to hang the notification tick on the same loop as the advisor's
  (`analyzer/scheduler.loop`): it already knows how to count the schedule from the previous
  tick and to survive restarts, while `plan_tick` is a pure function and is
  checked by tests without touching the network.
- [x] **D6. The "Advice" screen** - the history with statuses; dismissed tips travel into the
  next digest marked "already dismissed, do not repeat".
  `#/advice`: analyses with their tick cost, tips with a severity, support in
  numbers and the "accept / dismiss / restore" buttons. A dismissed one is not hidden -
  it is visible that a decision was made - while its fingerprint travels into the next tick's
  prompt. The "analyse now" button costs money, so it asks for a confirmation
  and shows the price. The interface is in Russian and English: the dictionary is shared with
  the other screens.
- [x] **D7. The M3 acceptance** - on a replay of the prototype project's history the advisor
  finds the mega-session, the idle turns and the heredoc pattern; a tick on haiku costs
  <= $0.02.
  Run over navuik/core for 30 days. All three were found:
  the mega-session `b2ae5a8a` (7,568 turns, $1,466, a context past the threshold) - marked
  as `crit` together with its chain worth $1,614, that is, 64% of the machine's spend;
  idle turns (506, 4%) linked to an overfilled context; the heredoc -
  `python3 <<` with 908 calls among 4,367 text bash commands, with a suggestion
  to move them into a skill. The free-form part of the acceptance is pinned by a test under
  the `real_history` marker - it checks that the digest sees those three things; the model
  call itself stays manual, because it costs money.
  **The threshold was not met: a tick cost $0.07 instead of $0.02.** The digest has nothing to
  do with it (1,608 tokens) - what is paid for is writing the Claude Code system prompt into
  the cache (11k tokens on every cold call). There is nothing to reduce:
  `--strict-mcp-config` and `--exclude-dynamic-system-prompt-sections` are already there. With
  an hourly tick that is ~$2 a day against $500+ of spend, an acceptable ratio, but the
  criterion from TZ §10 has to be corrected by fact rather than the implementation bent to fit.
  Noted for the future: haiku sometimes adds invented details to the support -
  in one tip it quoted wrong cache rates. The support in digest numbers is correct,
  while the reasoning around it needs checking; that is an argument for a stronger
  `weekly_deep_model` and for not showing the tips as truth.

## Milestone E - M4, OpenTelemetry

- [x] **E1.** Checking the current metric specification against
  https://code.claude.com/docs/en/monitoring-usage before the code.
  Checked on 14 August 2026; what was found is in the CLAUDE.md section about telemetry.
  The main divergence from the specification: port 4317 is gRPC, and it drags in `grpcio` and
  protobuf stubs. The `http/json` encoding is parsed by the standard json module, so the
  receiver gets by without new dependencies.
- [x] **E2.** The OTLP receiver, writing into secondary tables (not on top of the parser data).
  It lives right inside `cburn serve` on `POST /otlp/v1/{metrics,logs,traces}`, that is,
  on the same localhost:8799 - a second socket is not needed. The parsing lives in
  `collector/otlp.py`, the data in `otel_metrics`, `otel_events`, and the reception counters
  in `otel_ingest`; switching it on is printed by `cburn otel` (`--env`, `--settings`) -
  writing the variables for a human is impossible, `~/.claude` is read-only.
  The parsing is tolerant, like the JSONL parser's: an ununderstood piece of a payload counts
  as a loss and does not break the batch. A repeated payload is swallowed by the point
  fingerprint, otherwise an exporter retry would double the numbers. Personal attributes (the
  email, the account id, the organisation) are not stored in the database: we count spend, not
  people. Traces are acknowledged but not parsed - a 404 would drive the exporter into retries.
- [x] **E3.** Reconciling the OTel and JSONL metrics on a shared session - a difference <= 2%.
  Run over two live sessions (`tools/e3_compare.py`): on the main work
  (`query_source = main`) the difference is **0.00%** on the input, the output, both cache
  writes and the cost. The threshold is met with a margin, but what has to be compared is
  exactly `main`: Claude Code service requests (session title generation, ~535 tokens and
  $0.0006 per session) never reach the transcript at all, and on the total sum the
  difference reaches 98% on input tokens. The conclusion matters more than the reconciliation:
  **the transcript-based dashboard understates the spend**, and seeing that is possible only
  through OTel. The permission decisions (`tool_decision`) the D1 digest lacked come from
  there too. A test against live Claude Code is not pinned: it costs
  money, the reconciliation tool lives in `tools/`, and the payload parsing is covered by
  `tests/test_otlp.py`.
  The subscription limits need no refining any more: they arrive exact from
  `/api/oauth/usage` (task B3).
- [x] **E4. Putting the telemetry to use** - beyond the milestone plan: the collected data has
  to give something, otherwise the receiver just piles up tables.
  The dashboard gained a "past the transcripts" widget (the service spend and the
  permission confirmations by tool), the advisor digest gained the
  `off_transcript` and `permissions` sections, and the "waiting for permission" status is now
  decided by a `tool_decision` event rather than by the process tree: tools without a process
  of their own (MCP calls, `WebFetch`) no longer look like a hanging question.
  Everywhere it is a refinement on top of the transcripts: without telemetry everything is
  counted as before, and the digest sections are marked `available: false` - missing
  data must not be read by the advisor as zero confirmations.
  Two more things absent from the transcripts were added from the same events:
  the time spent inside every tool (on the "Session" screen -
  `duration_ms` from `tool_result`; the difference of timestamps in the JSONL lies, it
  includes waiting for a permission), and failed API requests (`api_error`,
  `api_refusal`) - the history shows only the answer that eventually arrived,
  so retries after a 429 and a 529 were invisible entirely. Telemetry reception
  is switched off from "Settings" rather than only by editing the config by hand.
  The `cburn stats` summary also stopped understating the spend silently: service requests
  and permission confirmations are printed as separate lines, but only when
  telemetry brought something. At the same time telemetry gained a retention
  (`otel.keep_days`, 30 days): 37 events for a one-minute session at ~400 bytes each -
  without a cleanup the tables outgrow the useful data.
  A measurement over 100,000 events (`tools/otel_bench.py`) showed that a day's telemetry
  slice costs 68 ms against 4 ms for all the rest of the overview, while the overview goes
  to subscribers every second - so the slice lives in a cache for five seconds, matching
  the exporter payload frequency.
  A privacy hole was closed separately: the `OTEL_LOG_USER_PROMPTS` variables
  and their relatives replace `<REDACTED>` with the real texts of prompts, answers
  and tool arguments, and the receiver would have quietly stashed them into the database. Now
  the content is dropped during parsing, and lengths and counters remain - and, by the way,
  the slash commands the parser does not see in the transcript come from them.
  The advisor also got the price of connecting MCP servers: a server starts
  anew in every session even if it was never called - on this machine
  two plugins add almost four seconds to every start, and that is visible
  only in telemetry.
  From the metrics came the active working time (pauses excluded) and the lines of code:
  next to the spend that answers the question of what came out of the money, and it goes
  to the advisor as the same section. The confirmation breakdown is limited to a dozen
  tools - a machine can carry dozens of MCP tools, and the tail would eat the digest
  budget.
  The most expensive finding came at the very end, in the events that were reached
  last: **hooks**. In a 66-second check session they took 50 -
  `Stop` 34.5 s and `UserPromptSubmit` 15.9 s, both HTTP to the telegram bridge, while
  `PreToolUse` and `PostToolUse` fit into 6-7 ms. In the transcript a hook is
  just a pause, and telling it from the model thinking is impossible in principle.
  Next to the time the declared hooks are listed: on this machine there are 13, every single
  one HTTP, including `PermissionRequest`, `SubagentStart` and
  `MessageDisplay`.
  The last layer is what a read-through of the finished code turned up: a busy database
  now answers 503 instead of a silent acknowledgement (otherwise the payload
  would be lost), an ununderstood payload lands in the loss counter and hints at
  `http/protobuf` instead of `http/json` (otherwise `cburn otel` said "there were no
  payloads" while telemetry worked), a per-project digest stopped mixing
  its active time with the machine-wide one, and the widget does not bring the dashboard down
  when the frontend is built newer than the running server - there is no error boundary in
  the frontend.

## Milestone F - M5, the desktop

- [x] **F1.** The Tauri wrapper around the finished frontend without edits to the frontend
  itself.
  The window loads `http://127.0.0.1:8799` - the same address that opens in a
  browser. The page is taken from the server rather than from local files: the frontend calls
  the API with relative paths, and from `tauri://` they would go nowhere. The frontend
  did not change by a single line - the "HTTP and WebSocket on localhost only" invariant
  held since milestone A paid off exactly here.
- [x] **F2.** The menu-bar tray: the burn rate switchable between thousand tokens/min and $/h,
  a red dot on an alert, three hot sessions, the last tip, the "Open the
  dashboard" and "Pause for 2 hours" items.
  The tray counts nothing itself - every five seconds it takes `/api/overview`, and it reads
  the alert threshold from `/api/config`, so that it obeys the same number that is edited
  in "Settings". The polling lives in its own thread: the menu bar refreshes even when there
  is no window. "Pause for 2 hours" silences the red dot in the tray - the telegram
  notifications were postponed together with D5 and live apart.
- [x] **F3.** Building a single `.app`, autostart instead of launchd.
  At start the application checks `/api/health` and raises the server itself if
  it stays quiet: the command is looked for by `CBURN_SERVE`, then in the usual install
  locations. Checked on a live run - the server turned out to be a child process of
  the `.app`. Autostart is toggled by a menu item (a LaunchAgent through the
  autostart plugin), so `cburn install` is no longer needed for the desktop scenario.
  The Python part is still installed separately: packing an interpreter
  inside the `.app` for a local tool is pointless, and that is a deliberate
  limitation rather than an omission. The `dmg` target was removed - `bundle_dmg.sh` fails on
  this machine, and an image is not required for "one installable `.app`".

## Verification

- `.venv/bin/python -m pytest -q`, `.venv/bin/ruff check . && .venv/bin/ruff format --check .`,
  `.venv/bin/mypy` - green before every commit.
- Milestone A: `cburn serve` + a live Claude Code session nearby, the needle refreshes in <= 1 s.
- Milestone B: `cburn session <id>` against a manual `jq` count over the same file -
  the difference is zero; `cburn reindex` walks all 590 MB without exceptions.
- Milestone D: a run of the advisor over the prototype project's history, a check of the tick
  cost and of the presence of the three expected tips; `/notify` is checked with curl before
  being wired into cburn.
- The read-only check: after a full run `find ~/.claude -newermt <run start>`
  shows no changes made by the application.

## Risks

- The transcript format is undocumented and has already diverged from the specification
  (`iterations`, `ephemeral_1h`, a dozen record types). We keep a tolerant parser, version
  fixtures and a smoke test over the whole history.
- The database is recreated on schema edits while there are no migrations. Until milestone C
  that is cheap (`cburn reindex`), after it a minimal schema version mechanism is needed.
- Task D4 touches a neighbouring working repository through which all Claude Code
  permissions go. The edit is made in a separate branch and checked against
  `/health` before the daemon is restarted.
