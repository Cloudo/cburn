# cburn

A local "speedometer" service for Claude Code token spend: it watches every
session on the machine, shows the burn rate in real time and once an hour
suggests optimisations. The full specification is `TZ.md` - the source of truth for
requirements, read it when a requirement is in question; this file holds the working
rules on top of it. The decomposition into tasks and their status live in
`.local/ROADMAP.md`; when closing a task, tick it there. That file is written in
Russian and stays out of git - see the rule about `.local/` below.

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

## Area knowledge lives in `.claude/rules/`

Everything specific to one corner of the code sits in a path-scoped rule: the
`paths:` frontmatter names the files it belongs to, and the rule is loaded only
when those files are touched. Consult the matching rule before changing an area,
and write new area facts there rather than here:

- `parser.md` - the parser invariants and the verified transcript format
- `otel.md` - the OTLP receiver and what telemetry gives beyond the transcripts
- `advisor.md` - the `claude -p` contract as installed today
- `actions.md` - the one write door: the plan, the diff, the backup, the rollback
- `liveness.md` - the session-to-process link and "waiting for permission"
- `limits.md` - the subscription limits endpoint
- `notifier.md` - the telegram notifications
- `frontend.md` - layout, themes and languages are the browser's business
- `desktop.md` - the Tauri window, the tray and the desktop build
- `tests.md` - rules for tests

## Invariants (never to be broken)

The area rules carry the invariants of their own corners; these hold everywhere:

- **`~/.claude` is read-only apart from one door.** The transcripts
  (`~/.claude/projects`) and `~/.claude.json` are never written to. What may be
  written is `settings.json` (the user one) and a project's `settings.local.json` -
  and only through `actions.py` (the reasons and the rollback rules are in
  `actions.md`). Everything else of ours lives in `~/.local/share/cburn/`.
- **Privacy.** The conversation text never leaves the machine. Only tool names, normalised
  commands, paths and numbers go into the advisor digest; including command fragments happens
  exclusively under the `allow_snippets` flag. The prompt log (`prompts`) is stored locally
  and shown on screen, and the digest knows nothing about it - what a human typed is for
  the human, not for the model.
- **Model prices live in the config, not in the code** (the `[prices]` section, the
  `model_prices` table). Hardcoding rates is not allowed.
- **`machine_id`** in `sessions` and `advice` is left empty in advance for a possible
  aggregation across several machines - do not remove it.
- **The frontend talks to the backend over HTTP/WebSocket on localhost only** - no direct
  filesystem access, no browser APIs missing from the system webview: the Tauri window
  loads the same page from the server.

## Decisions taken

They live in `DECISIONS.md` in the repository root - the option taken, the ones rejected
and the reason. A new decision is written down there, not here: this file keeps the rules
and the invariants.

## Conventions

- Heavy aggregates are computed in SQL, not in Python.
- Claude Code hooks are not used for collecting data - only the file watcher.
- Commits follow Conventional Commits (the `conventional-commits` skill), with messages in
  English.
- **Russian lives in the interface dictionaries and nowhere else:** `web/src/lib/dict.ts` for
  the dashboard and `src-tauri/dict.json` for the tray menu. Everything else - the code,
  the comments, the CLI output, the HTTP answers, the telegram notifications and the
  documentation - is English. The advisor prompt is English too, and the language of its
  answers is a setting (`analyzer.language`), not a hardcoded phrase.
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

## Milestones

| Stage | Content | Status |
| ---- | ---------- | ------ |
| M1 | The CLI: the JSONL parser, SQLite, `stats`/`sessions`/`session`, `reindex` | in progress |
| M2 | FastAPI + the web dashboard, live over WebSocket, `cburn serve`, launchd | - |
| M3 | The advisor (`claude -p`) and telegram notifications | done |
| M4 | The OTLP receiver, refined subscription limits, the weekly analysis | done |
| M5 | The Tauri wrapper and the menu-bar tray | done |

The acceptance criteria for every stage are in TZ §10.
