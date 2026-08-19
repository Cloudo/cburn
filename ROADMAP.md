# Roadmap

The milestones at a glance. Every requirement behind them is spelled out in the
[specification](TZ.md); the acceptance criteria are its section 10.

## Milestones

All five shipped in August 2026, 36 tasks of 36.

- [x] **M1 - the CLI core.** A tolerant parser for the undocumented transcript
  JSONL, SQLite, `cburn stats` / `sessions` / `session`, the whole history
  reindexed in seconds, figures that match a manual count to zero.
- [x] **M2 - the live dashboard.** FastAPI and WebSocket on localhost, the
  gauge, the sessions / session / advice / settings screens, autostart at login.
- [x] **M3 - the advisor and telegram.** An anonymised digest of the day,
  `claude -p` over it once an hour, notifications: the hourly digest, a spend
  spike alert, the daily summary.
- [x] **M4 - OpenTelemetry.** An OTLP receiver for what the transcripts do not
  carry: hidden spend, permission prompts, tool durations - cross-checked
  against the JSONL within 2%.
- [x] **M5 - the desktop.** A Tauri window and a macOS menu-bar tray with the
  figures you pick, one instance of each, one installable `.app`.

## Beyond the plan

The highlights of what kept arriving after the plan closed:

- [x] eighteen colour themes borrowed from VS Code, two interface languages,
  interface zoom
- [x] a live needle with exponential decay instead of a rectangular window
- [x] a draggable widget layout that lives in the browser, not on the server
- [x] single-instance guards for both the window and the server
- [x] a decisions journal, an app icon, this roadmap

## Ahead

- [ ] schema versioning and migrations instead of a full reindex after a schema
  change
- [ ] aggregation across several machines (`machine_id` is already reserved in
  the data)
