# Decisions

Why things are the way they are: the option taken, the options rejected and the
reason. Newest first. Things visible from the code and the git history do not
belong here; neither do the invariants, which live in `CLAUDE.md`.

## 2026-08-18 - CLAUDE.md keeps the core, the area knowledge moves to path-scoped rules

`CLAUDE.md` had grown to 410 lines and, together with the imported `TZ.md`,
loaded whole into every session. Now it keeps only what applies everywhere -
the stack, the structure, the global invariants and the conventions - and the
area knowledge (the transcript format, OTel, the advisor contract, the desktop
and the rest) lives in `.claude/rules/*.md` with `paths:` frontmatter, so a
rule enters the context only when the matching files are touched. The `@TZ.md`
import became a plain mention for the same reason: the spec is read when a
requirement is in question, not at every start.

Rejected: `@imports` (they expand at launch and save no context); skills
(these are facts about corners of the code, not workflows to invoke by name).

## 2026-08-15 - the colour themes are data, and the palettes come from VS Code

The dashboard had one dark palette and one light one, hardcoded in `styles.css`.
Instead of inventing a family of our own, the palettes are borrowed from the VS
Code themes of the same name, so the choice a person already made in the editor
is available here: eighteen of them, in `web/src/themes.ts` as a table of the
thirteen tokens `:root` declares.

Rejected: a stylesheet per theme (eighteen `[data-theme=...]` blocks would have
grown with every palette, and the swatches in the picker had nowhere to read the
colours from); a server-side setting (the layout, the language and the zoom are
all the browser's, and the API has no business knowing the colours).

The picker copies the editor's quick pick rather than a dropdown: the theme under
the cursor is applied to the whole page at once, because a palette is judged by
looking at it and not by its name. "Follow the system" switches between the last
dark and the last light theme picked, so choosing Dracula and Catppuccin Latte
once makes the macOS mode swing between exactly those two.

## 2026-08-15 - the decision journal is a file of its own

Decisions used to be a short section inside `CLAUDE.md`, mixed with the working
rules, so the reasoning behind a choice was never written down anywhere. From now
on every project keeps a `DECISIONS.md` in its root, and `CLAUDE.md` holds only
the rules and the invariants.

## Carried over from `CLAUDE.md` (taken earlier, the exact dates were not kept)

### The order of work is a vertical slice

First the end-to-end path "parser => SQLite => API => a dashboard with a needle",
then depth on M1 and M2. Building M1 to completion first would have left the whole
chain unproven until late.

### Telegram goes through the bridge, not straight to the Bot API

The main channel is the `/notify` endpoint added to the neighbouring
`~/code/cc-tg-bridge` project (the bridge had only `/hook` and `/health`). The
token then lives in one place instead of two. The direct Bot API stays as the
fallback channel in the config.
