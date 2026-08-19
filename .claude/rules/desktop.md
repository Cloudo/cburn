---
paths:
  - "src-tauri/**"
---

# The desktop (milestone F)

- **One copy of the instrument, and the guard is doubled.** Two trays over one server show
  one number twice, and two `cburn serve` read the transcripts twice and send the telegram
  alert twice. The window is held by `tauri-plugin-single-instance` (registered first of
  all the plugins): a second launch calls `tray::show_dashboard` in the first one and exits.
  The server is held by a `flock` on `~/.local/share/cburn/serve.lock`
  (`instance.only_one`) - the port does not do it, `--port 8800` would start a second
  watcher happily. The lock dies with the process, so a crash leaves nothing stale behind.
  The guard bites in a way that looks like a bug: with the demo app of `make demo-app` in
  the tray, `make desktop` hands its launch over to it and the demo window comes up on the
  demo dataset, while the dev build never opens at all. `make desktop` therefore refuses to
  start when another copy is running, and `demo-app.sh` asks the real one to quit before it
  takes the tray.
- **The window loads the page from the server, not from files.** The frontend calls the API
  with relative paths (`api/overview`); from `tauri://` they would go nowhere,
  so `frontendDist` points at `http://127.0.0.1:8799`. Thanks to that the
  frontend did not change by a single line for M5 - the acceptance criterion is met literally.
- **`CBURN_PORT` re-points the whole application at another instance.** The window URL is
  baked to `http://127.0.0.1:8799` at build time, so at start the window is navigated to
  the env port, and the tray poll, the pause POST and the health check follow the same
  address (`dashboard()` in `tray.rs`). `make demo-app` rests on it: the demo server takes
  :8798 and the real dashboard is left alone. The single-instance guard still holds - the
  script quits a running copy first, two applications at once are not possible.
- **The tray counts nothing itself:** every five seconds it takes `/api/overview`, and the
  alert threshold comes from `/api/config`, so that it obeys the same number that is edited
  in "Settings". The polling lives in its own thread: the menu bar lives without the window.
- **What the tray shows is the tray's own business, like the dashboard layout.** The figures
  of the icon title are ticked in the "menu bar shows" submenu (burn rate, $/h, the day, the
  percentages of the 5-hour and weekly windows), and the choice lies in
  `~/.local/share/cburn/tray.json` - the API knows nothing about it, exactly as it knows
  nothing about the widget positions. Keys are stored there rather than a bit mask: the file
  is read by a human and survives a change in the order of `METRICS`.
- **The tray speaks the language of the dashboard, and the language is still the browser's.**
  It lives in `localStorage` like the layout, and the tray cannot read that: on every switch
  the frontend mirrors the choice through `POST /api/ui/lang`, the server writes
  `~/.local/share/cburn/ui.json` (an atomic rename - the tray reads it whenever it likes),
  and the poll relabels the menu without a restart. The mirror is one-way, and the server
  takes no language of its own from it: texts still never come from the server. Until the
  dashboard has been opened once the menu is English - the tray has no `navigator.language`.
- **The `.app` raises the server when it stays quiet** (`CBURN_SERVE`, then the
  usual install locations). The interpreter is not packed inside the `.app` -
  a deliberate limitation: the Python part is installed separately, as before.
- **The `dmg` is not built:** `bundle_dmg.sh` fails on this machine, and an image is not
  needed for "one installable `.app`". Only `app` is left in `bundle.targets`.
- **Rust was installed with `--no-modify-path`** - the toolchain lives in `~/.cargo`, the
  shell profile was left alone; the build is called as
  `PATH=$HOME/.cargo/bin:$PATH npm run desktop:build`.
- **In dev mode the window is fed by vite, in the build by the server.** `devUrl` points
  at `http://localhost:5173` and `beforeDevCommand` starts vite, so `make desktop` gives
  the same hot reload inside the window as in a browser; the API and the WebSocket are
  proxied by vite onto the running `cburn serve`, which still has to be up. The port is
  fixed with `strictPort`: `tauri dev` waits for exactly that address, and a silent hop to
  5174 would leave it waiting forever. `frontendDist` is left pointing at the server - the
  built `.app` loads the page from `http://127.0.0.1:8799` as before.
- **The desktop build runs from the repository root, not from `web/`.** Tauri looks for
  `src-tauri/` next to itself, while `web/` holds only the frontend - from there the command
  fails with "Couldn't recognize the current folder as a Tauri project".
