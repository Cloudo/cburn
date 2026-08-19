//! The desktop wrapper of the dashboard (milestone F, SPEC §11 M5).
//!
//! The window loads the very same frontend from `http://127.0.0.1:8799` that opens
//! in a browser: the frontend does not change for this - from the very beginning it talks
//! to the backend over HTTP and WebSocket on localhost only (a project invariant). Because
//! of that the page is taken from the server rather than from local files: relative paths
//! like `api/overview` must land in our own server, not in `tauri://`.
//!
//! The menu-bar tray (SPEC §5) is the second way to look at the instrument: the spend figure
//! is always in sight, and the menu answers the question "what is happening now" without
//! requiring the window to be opened.
//!
//! The application runs in a single copy. A second one would raise a second tray icon next
//! to the first and a second window over the same server - two instruments showing one
//! number. Launch Services keeps a bundle from starting twice only when it is started the
//! usual way; `open -n`, a build from the sources next to an installed copy and the
//! autostart agent get past that, so the guard lives here: the second process hands the
//! launch over to the first and exits.

mod dict;
mod server;
mod tray;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // The single-instance plugin is registered first, as the documentation requires:
        // the callback must be in place before any other plugin has started anything.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            log::info!("a second launch: raising the window of the one already running");
            tray::show_dashboard(app, "");
        }))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            // The server raises itself when absent: an .app in autostart
            // replaces the launchd agent, and without a server the window is empty (F3).
            if let Some(path) = server::start_if_needed() {
                log::info!("dashboard server started: {}", path.display());
            }
            tray::setup(app.handle())?;
            // `CBURN_PORT` points the application at another instance (the demo dataset),
            // while the window URL is baked to the real port at build time - so the
            // window is sent to the right address here, at start.
            if std::env::var("CBURN_PORT").is_ok() {
                use tauri::Manager;
                if let Some(window) = app.get_webview_window("main") {
                    if let Ok(url) = format!("{}/", tray::dashboard()).parse() {
                        let _ = window.navigate(url);
                    }
                }
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("could not start the dashboard window");
}
