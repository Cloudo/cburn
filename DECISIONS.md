# Decisions

Why things are the way they are: the option taken, the options rejected and the
reason. Newest first. Things visible from the code and the git history do not
belong here; neither do the invariants, which live in `CLAUDE.md`.

## 2026-08-19 - a widget's height follows its content until a hand touches it

The default layout pinned every widget's height, and short content left dead
space at the bottom. Now an untouched widget fits itself to what it shows - a
`ResizeObserver` on an inner wrapper measures the natural content height, and
the grid `h` follows it, on first open and on every content change alike. The
moment the user grabs the resize corner the widget joins the `sized` list next
to the layout in `localStorage` and the fit backs off for good; per widget, not
globally, so tuning one card does not freeze the rest. The reset button clears
the list and hands the heights back to the fit.

Rejected: fitting only on the first open (live widgets change their content
every minute, and the gap would reopen); one global "touched" flag (resizing a
single widget would freeze all ten); inferring "touched" by comparing the saved
layout against the default (react-grid-layout saves a layout on the very first
mount, so a saved layout proves nothing about hands).

## 2026-08-19 - the instrument scale is a power, not a logarithm

Four orders of magnitude have to fit on one semicircle, so the scale has to be
compressed - but a plain logarithm gives every decade the same quarter of the arc,
and the needle of a working machine lives in the top one. A five sits at 70% of its
decade rather than in the middle, so 5 M pressed against 10 M with 13 degrees between
them. The curve is now `value ** 0.15`, normalised over 1 K...10 M: the top decade
takes 70 degrees instead of 45, and 5 M stands 24 degrees off the end. It is written
as a number of its own next to the decades - between a million and ten the needle
would otherwise be read by guessing.

Rejected: marks at the root of ten (3 K, 30 K, 300 K, 3 M land almost exactly in the
middle of a decade and space out evenly, but the numbers a person names are fives and
tens, not threes); cutting the range to 10 K...10 M (widens every decade to 60 degrees
and costs the whole bottom one); pale half-marks between the decades (tried and taken
away - the eye counts the marks it can read, and grey strokes only litter the dial).
All the variants were built behind a picker in the widget header and looked at side by
side; the picker went away with the choice made.

## 2026-08-18 - the needle turns around a zero, not around a pixel

The instrument needle rotated about `transform-origin: 200px 190px`, the hub in
viewBox units. WebKit multiplies that length by the interface zoom, so at the 1.25
rung the pivot moved to (250, 237.5) and the needle went flying off the dial with
the arc left empty; Chromium resolves the same declaration correctly, which is why
the fault only showed in the desktop window and in Safari. Now a `<g>` carries the
hub to the local origin with an SVG `transform` attribute - user units, no CSS
lengths involved - and the needle turns about `0 0`, the one length no zoom can
spoil.

Rejected: the SVG attribute `transform="rotate(a cx cy)"` on the needle itself
(equally immune, but the CSS transition then interpolates a matrix rather than an
angle, and the tip would leave the arc mid-swing - checked, a `<g>` plus a CSS
rotation keeps the radius at exactly 130 units through the whole 0.9 s);
percentages in `transform-origin` (they land on the right point, but a reader has
to divide 190 by 232 to see the hub in `81.9%`).

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
