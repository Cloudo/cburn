---
paths:
  - "web/src/**"
---

# The dashboard frontend

- **`web/src` is three folders, and no more.** `screens/` - the hash-routed screens
  (`Dashboard.tsx`, the widget grid, is one of them), `components/` - widget bodies and
  shared pieces (`Gauge`, `Profile`, `Help`, `StatusHelp`, `ThemePicker`), `lib/` - the
  non-visual modules (`api`, `dict`, `format`, `i18n`, `layout`, `theme`, `themes`,
  `zoom`). Only the entry files stay at the root: `main.tsx`, `App.tsx`, `styles.css`.
  A new file goes into one of the three; a new folder needs a reason.
- **Room for Tauri (M5).** The frontend talks to the backend over HTTP/WebSocket on localhost
  only - no direct filesystem access; no browser APIs missing from the system webview. The
  wrapper in M5 must not require reworking the frontend.
- **The dashboard layout is the browser's business, not the server's.** Positions, sizes and
  hidden widgets live in `localStorage` (`cburn.layout.v3`, the keys of the former project
  name are read as a fallback); the API knows nothing about them. The markup inside a widget
  reacts to its own width through `@container` rather than to the window width: widgets are
  dragged by hand while the window does not change.
- **The colour theme is data, and the choice is the browser's business too.** The eighteen
  palettes live in `web/src/lib/themes.ts` as a table of thirteen tokens - the same ones
  `styles.css` declares in `:root` - and are borrowed from the VS Code themes of the same
  name, so the dashboard offers the choice a person already made in the editor. The chosen
  theme writes those tokens onto `<html>`, and the stylesheet keeps only the fallback palette
  and what depends on the kind alone (the row tint, the shadows). The choice lies in
  `localStorage` next to the layout and the language: the id in `cburn.theme`, and beside it a
  remembered dark and light theme, which is what "follow the system" switches between. A
  colour written into the code instead of a token would stay at the old theme - that is why
  the chart slices read `var(--steel)` rather than a hex.
- **A pixel inside an SVG is not safe: the interface zoom multiplies it.** The scale is set
  through the CSS `zoom` of the root element, and WebKit (the desktop window and Safari)
  multiplies a CSS length by it while the viewBox coordinate system stays as it is - a
  `transform-origin: 200px 190px` on the instrument needle turned into (250, 237.5) at the
  1.25 rung and sent the needle off the dial, invisibly to Chromium. So a pivot is either a
  bare `0 0`, with the point carried there by an SVG `transform` attribute (user units,
  outside CSS lengths), or it is not written in pixels at all.
- **Texts the frontend shows never come from the server.** The server sends data (a limit
  window `kind`, a dictionary key for a failed request), and the words around it are built
  by `dict.ts`: the interface has two languages, and the backend has none.
- **The two dictionaries are the same device.** `web/src/lib/dict.ts` for the dashboard and
  `src-tauri/dict.json` for the tray menu: a key and a pair of languages, the reader picks
  one. They are data, not code - the tray pairs are baked into the binary by `include_str!`,
  so the menu needs no build step of its own.
