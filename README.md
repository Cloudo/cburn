# cburn

A local "speedometer" for Claude Code: it watches every session on the machine,
shows token spend in real time and once an hour suggests concrete
optimisations (what to move into a skill, where to fix permissions, which MCP to
switch off, when it is time to run `/clear`).

The full specification is in [TZ.md](TZ.md).

## Status

Milestone M1 (the CLI prototype: the JSONL parser, SQLite, `cburn stats/sessions/session`) is
in progress. Right now the repository holds the skeleton: the database schema, the config,
the CLI and smoke tests.

## Installing for development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Commands

```bash
cburn paths      # where the config, the database and the transcripts live
cburn initdb     # create the database and apply the schema
cburn reindex    # read the transcripts; --full re-reads them whole, --project narrows it down
cburn prices     # apply the config prices; --init puts a rate template there
cburn events     # unknown record types; --show <type> shows samples
cburn stats      # the spend summary; --period 7d|today|24h|30d|all|date, --project part of a slug
cburn sessions   # the session list, the same filters plus -n
cburn session ID # session details: totals, subagents, the work line, tools
cburn serve      # the dashboard on http://localhost:8799
cburn install    # autostart at login (launchd), uninstall removes it, status reports
cburn otel       # Claude Code telemetry: what arrived; --env and --settings switch it on
```

## Claude Code telemetry

Besides the transcripts, Claude Code has an official export of metrics and events over
OTLP. The receiver lives inside the dashboard itself (`POST /otlp/v1/metrics` and `/v1/logs`
on the same port), and the data lands in separate tables next to the parser data.

It is switched on by Claude Code's own environment variables - the application cannot write
them for you, it opens `~/.claude` read-only:

```bash
cburn otel --env       # export lines for the shell profile
cburn otel --settings  # the same set as a snippet for ~/.claude/settings.json
cburn otel             # what arrived: payloads, metrics, events, volume, session reconciliation
cburn otel --prune     # remove data older than otel.keep_days
```

Events arrive several per turn and per tool call, so they have a retention of their
own - `otel.keep_days`, 30 days by default (0 keeps everything). The cleanup runs by itself
at dashboard start and once a day; it does not touch the parser data. The database file does
not shrink after a cleanup - SQLite keeps the freed space for itself and fills it with new
records; to give the space back to the system, run
`sqlite3 ~/.local/share/cburn/cburn.db 'VACUUM;'` with the dashboard stopped.

After the edit Claude Code has to be restarted: it reads the environment at start.

Why this matters beyond accuracy: service requests (session title generation, for instance),
permission decisions and tool durations never reach the transcript - all of that is visible
only here.

What appears once telemetry is on:

- the "past the transcripts" widget on the dashboard - the spend of service requests, how many
  times work stopped for a manual confirmation (broken down by tool) and how often you went
  into another permission mode;
- the same two things in the advisor digest: it sees that the other numbers are understated
  and can suggest a concrete `permissions` fix;
- "waiting for permission" in the session list is decided by an event rather than by the
  process tree, so MCP calls and `WebFetch` no longer look like a hanging question;
- the session screen shows the time spent inside each tool: the difference between the
  timestamps in the history lies, it includes waiting for a permission too;
- failed API requests become visible - the history keeps only the answer that eventually
  arrived, so retries after a 429 or a 529 used to be invisible;
- it becomes visible how long the work actually ran (without pauses) and how many lines of
  code came out of it: the spend is neither good nor bad on its own - what matters is what
  was done for it. The same numbers go to the advisor;
- it becomes visible how much time hooks eat. In the history files a hook is just a pause,
  and waiting on an HTTP hook is indistinguishable from the model thinking: in my
  66-second check session the hooks took 50.

Without telemetry everything works as before - it only refines things.

Conversation texts never reach the database, even if you switched their logging on with
`OTEL_LOG_USER_PROMPTS` and the like: prompts, model answers, tool arguments and request
bodies are dropped during parsing, and only lengths and counters remain.

## The desktop application

The same dashboard, but as an ordinary macOS window and with the spend figure in the menu bar.

The commands are run **from the repository root**, not from `web/`: the latter holds only the
frontend, while Tauri looks for a `src-tauri/` directory next to itself.

```bash
npm install                          # the Tauri CLI (Rust is needed: rustup.rs)
PATH=$HOME/.cargo/bin:$PATH npm run desktop:build
open src-tauri/target/release/bundle/macos/cburn.app
```

`npm run desktop` is the same thing but with hot reload on Rust edits.
The path to `cargo` in the command is needed when Rust was installed with `--no-modify-path`
and is not in the shell profile.

The window loads `http://127.0.0.1:8799` - the same frontend as in a browser, so the
server has to be running. If it stays quiet, the application brings it up itself:
it takes the command from `CBURN_SERVE`, and without it looks for `cburn` in `~/.local/bin`,
homebrew and the development directory. The Python part is installed as usual meanwhile -
the interpreter is not packed inside the `.app`.

The menu bar shows the burn rate (switchable to $/h), a red dot lights up
when the spend goes above the threshold from "Settings", and the menu holds today's total,
three live sessions with their statuses, "pause for 2 hours" and "start at login". The last
item replaces the launchd agent: if you use the application, `cburn install` is not needed.

## Telegram notifications

Three reasons to write, and all of them are about "look, something is off here": the hourly
summary, if the advisor found something above `info`; the daily digest after the appointed
hour (once a day); an instant alert when the spend is above the threshold or a session
context has passed the critical mark.

It is configured in the `[telegram]` section: `mode` is `bridge` (the default), `bot`
or `off`; `bridge_url` is the address of the neighbouring `cc-tg-bridge`, which has
`POST /notify` for this; `bot_token` and `chat_id` are for the direct Bot API;
`daily_summary_at` is the digest time in local hours.

One session does not wake you more often than once every half hour. The "pause for 2 hours"
item in the menu bar (or `POST /api/notify/pause`) holds the silence, but `crit` passes
through it: the pause means "do not bother me with small things", not "switch the instrument
off".

## Autostart

`cburn install` puts a user agent into
`~/Library/LaunchAgents/com.cloudo.cburn.plist` and starts it right away:
the dashboard comes up at login and survives a reboot. The logs are in
`~/.local/share/cburn/serve.log`, next to the database. `cburn status` shows
what launchd thinks about the agent, `cburn uninstall` removes it and deletes the plist.

A user agent, not a system daemon: the dashboard reads `~/.claude` and writes
into its own directory, root is not needed for that.

## Moving from the former name

The project used to be called `cloudo-dash`, and the command `cdash`. The state moves itself
on the first start: `~/.local/share/cloudo-dash` becomes `~/.local/share/cburn`
(the database together with `-wal` and `-shm` is renamed to `cburn.db`), and
`~/.config/cloudo-dash` becomes `~/.config/cburn`. The dashboard layout, theme and language
are read from the former `localStorage` keys and saved under the new ones.

Autostart is moved by hand: the agent label changed to `com.cloudo.cburn`,
so the old one is removed with `launchctl bootout gui/$UID/com.cloudo.cloudo-dash` and by
deleting `~/Library/LaunchAgents/com.cloudo.cloudo-dash.plist`, while the new one is
installed with `cburn install`.

## How long the first indexing takes

A measurement on a MacBook (Apple Silicon, SSD), 13 August 2026: **639 MB of transcripts,
459 files, 185,799 lines, 17,186 turns - 5.4 seconds** from scratch. After that only the
tail is read from the stored offset, so a repeated `cburn reindex` fits into tenths of a
second.

Hence the decision: a background job with progress in the API, batched inserts and a worker
pool are not needed - the "longer than a few minutes" threshold from the plan is not even
close. `cburn reindex` shows a live walk line in the terminal, and that is enough.

## Checks

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
```

The smoke test over the real history is off by default: it depends on the machine rather
than on the repository. Run it by hand after a Claude Code update - it parses the whole
`~/.claude/projects` and prints a census of record types:

```bash
.venv/bin/python -m pytest -m real_history -q -s
```

## Privacy

`~/.claude` is opened strictly read-only. The conversation content never leaves the machine:
only the aggregated digest going into `claude -p` and the text of telegram notifications
go out.
