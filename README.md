<p align="center">
  <img src="src-tauri/icons/app-icon.png" alt="cburn" width="88" />
</p>

<h1 align="center">cburn</h1>

<p align="center">
  A local speedometer for Claude Code token spend.
</p>

<p align="center">
  <a href="LICENSE"><img alt="license MIT" src="https://img.shields.io/github/license/cloudo/cburn?color=4c8eda" /></a>
  <img alt="macOS 13+" src="https://img.shields.io/badge/macOS-13%2B-1a1a1a?logo=apple&logoColor=white" />
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white" />
</p>

<p align="center">
  <img src="media/demo.webp" alt="the needle moving and the palettes switching" width="920" />
</p>

<p align="center">
  <sub><a href="media/demo.mp4">The whole tour</a> - a minute and a bit over the demo
  dataset: the gauge, the palettes, the sessions and the advisor.</sub>
</p>

cburn watches every Claude Code session on the machine and turns the transcripts
into a live picture of where the tokens go. The needle climbs while Claude works
and glides down in silence; around it - the day's totals, the cost at API prices,
the subscription limit windows and the sessions in flight.

Everything happens locally: the transcripts are read straight from
`~/.claude/projects`, and the conversation text never leaves the machine.

## Install

There is no packaged build yet - `brew install` and a ready `.app` in the releases
are the next thing on the list. Today cburn is built from source:

```bash
git clone https://github.com/cloudo/cburn.git && cd cburn
make install   # .venv, an editable install and the npm dependencies
make web       # build the frontend into web/dist
make reindex   # read the transcripts into the database
make serve     # the dashboard on http://127.0.0.1:8799
```

`cburn install` adds autostart at login through launchd. The menu-bar application
is a separate build and wants Rust:

```bash
make desktop-build   # the .app lands in src-tauri/target/release/bundle/macos
```

It is not signed, so macOS holds it at arm's length the first time: open it from
the Finder with a right click and "Open", or lift the quarantine flag by hand.

```bash
xattr -dr com.apple.quarantine src-tauri/target/release/bundle/macos/cburn.app
```

## Requirements

macOS 13 or newer, Apple Silicon or Intel. Python 3.11+ and Node to build, Rust
only for the menu-bar application. And Claude Code itself, naturally: cburn reads
the transcripts it leaves in `~/.claude/projects` and never writes there.

## Why

Counting tokens after the fact is easy, and several tools do it. The question that
costs money is a different one: what is burning **right now**, and what to change
about it. cburn answers with a needle that moves within a second of a turn landing
in a transcript, with the subscription windows taken from Anthropic rather than
guessed, and with an advisor that reads an anonymised digest once an hour and says
where the context went to waste.

## What it does

- **Live gauge** - the burn rate over WebSocket, moving within a second of a turn landing in a transcript.
- **Cost and totals** - tokens by kind, dollars at API prices, models and tools of the day.
- **Subscription limits** - the 5-hour and weekly windows, refreshed from Anthropic.
- **Advisor** - once an hour `claude -p` reads an anonymised digest and suggests optimisations, with optional telegram notifications.
- **Menu-bar tray** - a Tauri wrapper puts the figures you pick into the macOS menu bar.
- **OTLP receiver** - takes Claude Code telemetry for what the transcripts do not carry.

## The screens

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="media/dashboard-dark.png" />
    <img src="media/dashboard-light.png" alt="the dashboard" width="920" />
  </picture>
</p>

<p align="center">
  <sub>The overview in the colours of your system. Eighteen palettes are borrowed
  from the VS Code themes of the same name, so the dashboard offers the choice you
  already made in the editor.</sub>
</p>

<p align="center">
  <img src="media/sessions.png" alt="the sessions screen" width="49%" />
  <img src="media/session.png" alt="a single session" width="49%" />
</p>

<p align="center">
  <sub>Every session with filters and a spark of its spend; inside one - the context
  by turn, the tools it leaned on and the whole turn feed.</sub>
</p>

<p align="center">
  <img src="media/advice.png" alt="the advice screen" width="49%" />
  <img src="media/settings.png" alt="the settings screen" width="49%" />
</p>

<p align="center">
  <sub>The advisor groups its tips by what they cost and remembers the ones you
  rejected; thresholds, models and the telegram channel are edited on the spot.</sub>
</p>

<p align="center">
  <img src="media/widgets.png" alt="the widget picker" width="49%" />
  <img src="media/tray.png" alt="the menu bar tray" width="34%" />
</p>

<p align="center">
  <sub>Widgets are dragged, resized and hidden, and the layout stays in the browser;
  the tray carries the figures you pick and the sessions waiting for you.</sub>
</p>

<p align="center">
  <sub>All the screenshots show a synthetic demo dataset - <code>make demo</code>
  generates it and serves a second dashboard without touching the real database.</sub>
</p>

## Telemetry (optional)

Claude Code can export OTLP metrics and events, and cburn ships a receiver for
them. Telemetry fills the gaps the history files cannot: service spend that
never reaches a transcript (session titles cost a separate haiku call), how
often work stopped for a permission prompt, exact request prices and durations,
time spent inside tools and hooks.

It is off by default because only Claude Code's own environment can switch it
on - cburn never writes into another program's config. Print the ready-made
lines, paste them into your shell profile or `settings.json` and restart
Claude Code:

```bash
cburn otel --env
```

Everything stays on the machine: the exporter points at the local receiver
inside `cburn serve`, and prompt or response texts are dropped at parsing -
only counters and durations are stored. Without telemetry the dashboard simply
counts the old way, from the transcripts alone.

## Privacy

Only tool names, normalised commands, paths and numbers reach the advisor; what
you typed stays on your screen. `~/.claude` is read-only for cburn - the single
exception is applying an advice you confirmed, always with a backup and a
rollback.

## FAQ

**The dashboard is empty after a clone.** The frontend is not committed: run
`make web` once, and `/` serves the page instead of a list of endpoints.

**"limits unavailable: no Claude Code token in the keychain".** The limits come
from the same endpoint `/usage` uses, authorised with the OAuth token Claude Code
keeps in the keychain. The message covers the case where the request never left
the machine as well - if the token is in place, the reason is in
`~/.local/share/cburn/serve.log`.

**`make desktop` refuses to start.** The application lives in a single copy, so a
second launch would be handed over to the one already in the tray. Quit it there
and run the command again.

**Where everything lives.** `cburn paths` prints it: the database and the log in
`~/.local/share/cburn`, the config in `~/.config/cburn/config.toml`. The
transcripts stay where Claude Code put them.

## Uninstall

```bash
cburn uninstall                              # the launchd agent
rm -rf ~/.local/share/cburn ~/.config/cburn  # the database, the log, the config
```

The application, if you moved it into `/Applications`, goes to the bin like any
other. Nothing of cburn is left inside `~/.claude`.

## Development

A bare `make` prints the whole toolbox: the checks (`make check` before every
commit), the hot-reload frontend, the desktop window, the telemetry receiver.
`make demo` raises a second dashboard over a synthetic dataset - the screenshots
above come from it. What is done and what lies ahead is in the
[roadmap](ROADMAP.md), and every requirement in detail is in the
[specification](SPEC.md).

## License

[MIT](LICENSE)
