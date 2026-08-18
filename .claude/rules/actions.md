---
paths:
  - "src/cburn/actions.py"
  - "src/cburn/api/**"
---

# The write door (`actions.py`)

Invariants:

- **`~/.claude` is written through this door only, and the reasons are hard.** The
  transcripts (`~/.claude/projects`) are the data source, and a write there moves the very
  offsets we read the tail from; `~/.claude.json` is live state Claude Code rewrites every
  few seconds, so a read-modify-write of ours would silently lose someone else's change.
  What may be written is `settings.json` (the user one) and a project's
  `settings.local.json` - nothing else.
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
- **`SessionEnd` hooks do not run on SIGTERM.** Claude Code has no signal handler of its own:
  `SIGINT`/`SIGHUP`/`SIGTERM` cause an immediate `process.exit()`, while hooks run
  asynchronously on a regular exit (`/exit`, Ctrl+D, `/clear`, logout). There is no
  "finish someone else's session" command in the CLI either, so the dashboard sends SIGTERM
  and warns about that honestly in the popover.
