# Decisions

Why things are the way they are: the option taken, the options rejected and the
reason. Newest first. Things visible from the code and the git history do not
belong here; neither do the invariants, which live in `CLAUDE.md`.

## 2026-08-21 - installed by the fully qualified name

Homebrew 6.0 stopped loading a non-official tap until it is trusted, and the two lines the
README had given since the first release - `brew tap cloudo/tap`, then `brew install cburn` -
now stop with `Refusing to load formula cloudo/tap/cburn from untrusted tap`. Two ways past
it. Telling the reader to run `brew trust cloudo/tap` was rejected: whole-tap trust covers
every formula the tap will ever hold, and asking a stranger for it so that one speedometer
can be installed is a bad trade, besides being a habit worth not teaching.

So the install is one line, `brew install cloudo/tap/cburn`. A fully qualified name trusts
that single formula, taps the repository on the way, and is shorter than what it replaces.
The README says why the long name is there, so that nobody shortens it back.

## 2026-08-20 - the digest counts, the model formulates

Three new detectors raised the question of where a tip is born. A rule in Python can
carry its own wording - cheaper by a whole call, and the answer is instant. It was
rejected: a rule says the same sentence to everyone forever, and the tips that are worth
reading are the ones that put two numbers side by side ("the cache is rewritten on every
turn of the long sessions" is not a threshold, it is a reading of the digest as a whole).

So the division stays as it was. The digest counts and says nothing: `cache` weighs what
the five-minute writes came to, `compaction` weighs what the turn after a squeeze cost,
`subagents` weighs the share of the sidechains. The prompt explains what each number
means and where the line is - including the traps: the hour-long cache is not a blind
answer, and a compaction is normal work rather than a fault. What sentence to say is the
model's business, and it says it in the language of the person reading.

The price is one call an hour, which is already paid. The gain is that a new detector is
a query and a paragraph rather than a set of thresholds nobody dares to change.

## 2026-08-20 - no alert mark in the menu bar

SPEC asked for a red dot on an active alert, but macOS draws the tray title
monochrome: the dot rendered white, read as decoration rather than alarm, and
pushed the glyph out of line with the neighbouring icons. Dropped, together
with the `/api/config` threshold poll that fed it; alerts live in telegram and
on the dashboard, and "pause for 2 hours" still holds the telegram messages.
SPEC §5 is reworded accordingly.

## 2026-08-20 - progress fills animate transform, not width

The meter/limits/plan fills are full-width elements scaled with
`transform: scaleX(share)` instead of an animated `width`. Reason: in the
zoomed desktop webview an animated width left a one-pixel amber smear across
the empty part of the track (a repaint artifact of layout animation under CSS
`zoom`); a composited transform repaints the whole layer and cannot leave a
trail. Rejected: `will-change: width` / `translateZ(0)` (does not stop layout
repaints), `translateX` or `clip-path` on a full-width fill (the gradient
stops compressing with the bar and the fade all but disappears at low fills).
The cost of `scaleX` is a slightly squarer rounded cap at small shares.

## 2026-08-20 - the menu bar gets its own template glyph, not the app icon

The tray icon is a dedicated monochrome png (`src-tauri/icons/tray.png`, drawn
by `tools/tray_icon.py`) embedded with `include_bytes!`. Rejected: feeding
`default_window_icon()` into the template - macOS builds a template image from
the alpha channel alone, and the app icon's opaque rounded square rendered as a
featureless white blob; the speedometer never reached the menu bar at all. A
side gain: `include_bytes!` registers the file with cargo, so a changed glyph
rebuilds, while icons taken through `generate_context!` can survive a rebuild
stale.

## 2026-08-19 - the demo dataset goes through the real pipeline, not a mocked backend

The README screenshots need believable English data. Chosen: a generator
(`tools/demo_data.py`) writes synthetic transcripts in the verified JSONL
format, the real collector ingests them into a demo database, and the server is
pointed at the demo tree by environment - `CLAUDE_CONFIG_DIR` existed, and
`CBURN_CONFIG` / `CBURN_DATA_DIR` overrides were added to `paths.py`. What the
transcripts cannot carry (telemetry, advice) is seeded into the demo database
directly; `--tick` appends a fresh burst to the live sessions so the needle is
up at the moment of a screenshot.

Rejected: a mocked backend (every endpoint plus the WebSocket protocol would
have to be faked and kept in step, and the screenshots would stop showing the
real product); screenshotting the real database (private, Russian, and the
prompts would need blurring).

## 2026-08-19 - web/src is split by role: screens, components, lib

Twenty-one files lay flat in one directory. The split is by role, matching the
app's own shape (a shell with hash-routed screens filled by widgets): `screens/`
for the routed screens, `components/` for widget bodies and shared pieces,
`lib/` for the non-visual modules, entry files at the root. Rejected: feature
folders (a folder per screen with its own components - ceremony a dashboard of
this size does not repay) and a bare ts/tsx split (two piles say nothing about
what a file is for).

## 2026-08-19 - the specification is SPEC.md after all

The price the entry below refused to pay came to one pass of a replacement:
nine links and seventy-eight citations of the form "TZ §4" across thirty-nine
files, mechanical to the last one and covered by the test suite. "TZ" is
"техническое задание" written in Latin letters - the last thing in the
repository that its own rule about language forbids, and a name a reader
outside Russian cannot decode. The file has called itself "Specification" in
its first line from the very start; now the name says the same.

Rejected: `REQUIREMENTS.md` - it reads worse where the file is quoted most,
inside a comment: "SPEC §4" fits the line, "REQUIREMENTS §4" does not.

## 2026-08-19 - the public face of the plan is ROADMAP.md

An English reader now gets a checkbox roadmap (`ROADMAP.md`) instead of being
sent to the specification, whose name at the time - `TZ.md` - said nothing
outside Russian. Renaming the file itself was rejected here: some thirty files
cite it as "TZ §n", and a rename would buy a clearer filename at the price of
touching them all. The README links it as "the specification" instead, which
does the same job for the reader. (The rename happened anyway - see above.)

## 2026-08-19 - the ring compresses its shares: square roots and a floor sliver

Cache reads outrun the other parts by two orders of magnitude, so honest shares
painted the whole ring one colour, always. The arc widths are now proportional
to the square roots of the values - "200 times more" turns into "14 times more" -
and every nonzero part is guaranteed three degrees of the semicircle. The ring
thereby stops being an exact diagram and becomes a map of the colours; the
honest percentages stay a centimetre away in the legend and in the tooltips,
where a lifted sliver introduces itself as "<1%", not "0%".

Rejected: a logarithm (makes the parts look near-equal - a two-hundredfold gap
reads as none - and log of a handful of tokens jitters near zero); shares of
cost instead of tokens (the most meaningful cure, but the needle would stay in
tokens while the ring switched to dollars, and the server would have to price
the parts per model - too much instrument for one ring); leaving the ring
honest and always blue (the constant 99% carries no news, and the informative
ratio between the small parts was invisible).

## 2026-08-19 - the gauge needle is a decaying integrator, not a window

The picker offered rectangular windows (10 s ... 60 min), and none of them moved
like a car needle: the long ones froze it, and in a short one a turn's burst
either sat whole or dropped out at once, teleporting the needle instead of
letting it fall. Now the server ships a `live` burn entry - every turn is
weighted by `exp(-age / 30 s)`, a ~21-second half-life - so active work keeps
pushing the value up and silence lets it glide down, load-average style. The
gauge picker shrank to live / 5 s / 10 s / 1 min with live as the default; the
5-minute and hour windows left the picker but are still computed, because the
notifier threshold and the tray read the minute entry.

Rejected: an even shorter rectangular window (twitchy, not car-like); smoothing
the needle in the frontend only (the caption would lie about what the number
means); dropping the 1m/5m/60m windows from the API (the notifier and the tray
consume 1m, and SPEC §4 names them).

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

## 2026-08-19 - the magnitude suffix speaks the language of the interface

Large numbers were shortened US-style everywhere - K, M, B - and that was fine while
the numbers stood alone. As soon as a unit joined the reading of the instrument, the
word came out of two alphabets at once: "1,71 M/мин". Now `compact` takes the suffix
from the dictionary like every other word: "тыс", "млн", "млрд" in Russian, K, M and
B in English, so a number and the unit beside it are written in one script.

Rejected: a Latin unit to match the Latin suffix ("1.71 M/min" in a Russian
interface, with "ток/мин" already standing next to it in the same widget); Russian
suffixes on the instrument alone (the breakdown lies a centimetre away and would keep
saying "1,69 M"). The columns grew by a couple of characters - checked on the
dashboard, in the session list and on the dial of the instrument, nothing overflows.

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

`CLAUDE.md` had grown to 410 lines and, together with the imported `SPEC.md`,
loaded whole into every session. Now it keeps only what applies everywhere -
the stack, the structure, the global invariants and the conventions - and the
area knowledge (the transcript format, OTel, the advisor contract, the desktop
and the rest) lives in `.claude/rules/*.md` with `paths:` frontmatter, so a
rule enters the context only when the matching files are touched. The `@SPEC.md`
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
