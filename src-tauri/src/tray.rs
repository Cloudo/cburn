//! The menu-bar tray: the spend always in sight (task F2, TZ §5).
//!
//! The data comes from the same `/api/overview` as the dashboard - the tray counts
//! nothing itself. Polling runs every `POLL` seconds: more often is pointless, the figure
//! in the menu bar is read by eye, not measured.
//!
//! What the icon title shows: the burn rate of output tokens per minute,
//! or the cost per hour - switched by a menu item. A red dot before the
//! figure means the spend is above the `thresholds.burn_rate_warn_per_min`
//! threshold from the config; it can be silenced for two hours by the "pause for 2 hours"
//! item - that is silence in the tray only, telegram notifications live apart (D5).

use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Runtime, WebviewUrl, WebviewWindowBuilder};

/// The dashboard address: the same one that opens in a browser.
pub const DASHBOARD: &str = "http://127.0.0.1:8799";

/// How often to ask for the overview. An instrument in the menu bar is no stopwatch: five
/// seconds is enough for the figure to look alive, and it is four times cheaper than the frontend tick.
const POLL: Duration = Duration::from_secs(5);

/// How long the silence from the "pause for 2 hours" item lasts (TZ §5).
const PAUSE: Duration = Duration::from_secs(2 * 60 * 60);

/// How many sessions to show in the menu: the three hottest, beyond that the list
/// stops being readable at a glance.
const HOT_SESSIONS: usize = 3;

/// What the icon title shows and until when the alert stays quiet.
struct State {
    /// `true` means money per hour, `false` means thousands of tokens per minute.
    show_cost: AtomicBool,
    /// Unix time until which the red dot is not shown.
    quiet_until: AtomicI64,
}

impl State {
    fn is_quiet(&self) -> bool {
        self.quiet_until.load(Ordering::Relaxed) > unix_now()
    }
}

fn unix_now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs() as i64)
        .unwrap_or(0)
}

/// Menu items refreshed on every poll.
struct Items<R: Runtime> {
    burn: MenuItem<R>,
    today: MenuItem<R>,
    sessions: Vec<MenuItem<R>>,
    advice: MenuItem<R>,
    unit: MenuItem<R>,
    pause: MenuItem<R>,
    autostart: MenuItem<R>,
}

pub fn setup<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let state = Arc::new(State {
        show_cost: AtomicBool::new(false),
        quiet_until: AtomicI64::new(0),
    });

    let burn = MenuItem::with_id(app, "burn", "burn rate: -", false, None::<&str>)?;
    let today = MenuItem::with_id(app, "today", "today: -", false, None::<&str>)?;
    let advice = MenuItem::with_id(app, "advice", "no tips yet", true, None::<&str>)?;
    let unit = MenuItem::with_id(app, "unit", "show $/h", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "pause for 2 hours", true, None::<&str>)?;
    let autostart = MenuItem::with_id(app, "autostart", autostart_label(app), true, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "open the dashboard", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "quit", true, None::<&str>)?;
    let sessions: Vec<MenuItem<R>> = (0..HOT_SESSIONS)
        .map(|index| MenuItem::with_id(app, format!("session-{index}"), "—", false, None::<&str>))
        .collect::<tauri::Result<_>>()?;

    let separator = PredefinedMenuItem::separator(app)?;
    let mut entries: Vec<&dyn tauri::menu::IsMenuItem<R>> = vec![&burn, &today, &separator];
    for item in &sessions {
        entries.push(item);
    }
    entries.extend([
        &separator as &dyn tauri::menu::IsMenuItem<R>,
        &advice,
        &separator,
        &unit,
        &pause,
        &autostart,
        &open,
        &quit,
    ]);
    let menu = Menu::with_items(app, &entries)?;

    let items = Arc::new(Items {
        burn,
        today,
        sessions,
        advice,
        unit,
        pause,
        autostart,
    });

    let handler_state = Arc::clone(&state);
    let handler_items = Arc::clone(&items);
    let tray = TrayIconBuilder::with_id("cburn")
        .icon(app.default_window_icon().unwrap().clone())
        .icon_as_template(true)
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(move |app, event| {
            on_menu(app, &event, &handler_state, &handler_items);
        })
        .build(app)?;

    // Polling lives in its own thread: refreshing the menu bar must not depend on
    // whether the window is open, and there may be no window at all.
    let poll_app = app.clone();
    let poll_state = Arc::clone(&state);
    let poll_items = Arc::clone(&items);
    std::thread::spawn(move || loop {
        match fetch_overview() {
            Ok(overview) => apply(&tray, &poll_state, &poll_items, &overview),
            Err(error) => {
                let _ = tray.set_title(Some("—"));
                let _ = poll_items.burn.set_text(format!("no connection to cburn serve: {error}"));
            }
        }
        let _ = &poll_app;
        std::thread::sleep(POLL);
    });

    Ok(())
}

fn on_menu<R: Runtime>(
    app: &AppHandle<R>,
    event: &MenuEvent,
    state: &Arc<State>,
    items: &Arc<Items<R>>,
) {
    match event.id.as_ref() {
        "quit" => app.exit(0),
        "open" => show_dashboard(app, ""),
        "autostart" => {
            toggle_autostart(app);
            let _ = items.autostart.set_text(autostart_label(app));
        }
        "advice" => show_dashboard(app, "#/advice"),
        "unit" => {
            let cost = !state.show_cost.load(Ordering::Relaxed);
            state.show_cost.store(cost, Ordering::Relaxed);
            let _ = items.unit.set_text(if cost {
                "show k tok/min"
            } else {
                "show $/h"
            });
        }
        "pause" => {
            let quiet = state.is_quiet();
            state.quiet_until.store(
                if quiet { 0 } else { unix_now() + PAUSE.as_secs() as i64 },
                Ordering::Relaxed,
            );
            // The same pause goes to the server too: it silences not only the red dot
            // in the menu bar but also the telegram messages (D5).
            let _ = ureq::post(&format!(
                "{DASHBOARD}/api/notify/pause?on={}",
                if quiet { "false" } else { "true" }
            ))
            .timeout(Duration::from_secs(3))
            .call();
            let _ = items.pause.set_text(if quiet {
                "pause for 2 hours"
            } else {
                "resume notifications"
            });
        }
        id => {
            // Sessions are labelled by their own id: `session-0` and so on,
            // while the session id itself sits in the item tooltip.
            if let Some(index) = id.strip_prefix("session-").and_then(|n| n.parse::<usize>().ok()) {
                if let Some(session) = items.sessions.get(index) {
                    if let Ok(text) = session.text() {
                        if let Some(id) = text.split_whitespace().next() {
                            show_dashboard(app, &format!("#/session/{id}"));
                        }
                    }
                }
            }
        }
    }
}

/// Show the dashboard: an open window is raised, otherwise it is created anew.
/// A second launch of the application ends up here as well - instead of its own window.
pub fn show_dashboard<R: Runtime>(app: &AppHandle<R>, hash: &str) {
    let url = format!("{DASHBOARD}/{hash}");
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
        if !hash.is_empty() {
            if let Ok(parsed) = url.parse() {
                let _ = window.navigate(parsed);
            }
        }
        return;
    }
    if let Ok(parsed) = url.parse() {
        let _ = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(parsed))
            .title("cburn")
            .inner_size(1180.0, 860.0)
            .build();
    }
}

/// The alert threshold from the dashboard config: keeping a copy of it in the tray is not
/// allowed - a human edits the thresholds in "Settings", and the tray must obey that same number.
fn warn_threshold() -> f64 {
    ureq::get(&format!("{DASHBOARD}/api/config"))
        .timeout(Duration::from_secs(3))
        .call()
        .ok()
        .and_then(|response| response.into_json::<serde_json::Value>().ok())
        .and_then(|config| {
            config
                .pointer("/config/thresholds/burn_rate_warn_per_min")
                .and_then(|value| value.as_f64())
        })
        .unwrap_or(0.0)
}

fn fetch_overview() -> Result<serde_json::Value, String> {
    let response = ureq::get(&format!("{DASHBOARD}/api/overview"))
        .timeout(Duration::from_secs(3))
        .call()
        .map_err(|error| error.to_string())?;
    response
        .into_json::<serde_json::Value>()
        .map_err(|error| error.to_string())
}

fn apply<R: Runtime>(
    tray: &tauri::tray::TrayIcon<R>,
    state: &Arc<State>,
    items: &Arc<Items<R>>,
    overview: &serde_json::Value,
) {
    let burn = overview.pointer("/burn/1m").unwrap_or(&serde_json::Value::Null);
    let output = burn.get("output_per_min").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let cost_hour = burn.get("cost_per_hour").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let tokens = burn.get("tokens_per_min").and_then(|v| v.as_f64()).unwrap_or(0.0);
    // The threshold is asked for once per tick: two calls in a row would cost
    // two extra requests to our own server every five seconds.
    let threshold = warn_threshold();
    let alert = threshold > 0.0 && tokens >= threshold;

    let value = if state.show_cost.load(Ordering::Relaxed) {
        format!("${cost_hour:.2}/h")
    } else {
        format!("{:.1}K/min", output / 1000.0)
    };
    // The red dot is the only alert in the menu bar: the icon cannot be given
    // a colour, and a dot before the figure is noticeable without hindering reading.
    let title = if alert && !state.is_quiet() {
        format!("● {value}")
    } else {
        value.clone()
    };
    let _ = tray.set_title(Some(&title));

    let _ = items.burn.set_text(format!(
        "burn rate: {:.1}K tok/min · ${:.2}/h",
        output / 1000.0,
        cost_hour
    ));
    let today_cost = overview
        .pointer("/today/cost_usd")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    let today_turns = overview
        .pointer("/today/turns")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let _ = items
        .today
        .set_text(format!("today: {today_turns} turns · ${today_cost:.2}"));

    let empty = Vec::new();
    let sessions = overview
        .get("live_sessions")
        .and_then(|v| v.as_array())
        .unwrap_or(&empty);
    for (index, item) in items.sessions.iter().enumerate() {
        match sessions.get(index) {
            Some(session) => {
                let id = session.get("id").and_then(|v| v.as_str()).unwrap_or("—");
                let project = session
                    .get("project")
                    .and_then(|v| v.as_str())
                    .unwrap_or("—");
                let status = session.get("status").and_then(|v| v.as_str()).unwrap_or("—");
                let _ = item.set_text(format!("{id:.8} {project} — {}", status_label(status)));
                let _ = item.set_enabled(true);
            }
            None => {
                let _ = item.set_text("—");
                let _ = item.set_enabled(false);
            }
        }
    }

    let advice_ticks = overview
        .pointer("/advisor/ticks")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let _ = items.advice.set_text(if advice_ticks > 0 {
        format!("advice: {advice_ticks} analysis(es) today")
    } else {
        "no tips yet".to_string()
    });
}

/// The caption of the autostart item: it also shows the current state.
fn autostart_label<R: Runtime>(app: &AppHandle<R>) -> &'static str {
    if is_autostart_on(app) {
        "do not start at login"
    } else {
        "start at login"
    }
}

fn is_autostart_on<R: Runtime>(app: &AppHandle<R>) -> bool {
    use tauri_plugin_autostart::ManagerExt;
    app.autolaunch().is_enabled().unwrap_or(false)
}

/// Switch autostart on or off. The error is not hidden silently: without rights to
/// LaunchAgents the caption simply will not change, and that is visible in the menu.
fn toggle_autostart<R: Runtime>(app: &AppHandle<R>) {
    use tauri_plugin_autostart::ManagerExt;
    let manager = app.autolaunch();
    let result = if is_autostart_on(app) {
        manager.disable()
    } else {
        manager.enable()
    };
    if let Err(error) = result {
        log::warn!("autostart did not toggle: {error}");
    }
}

fn status_label(status: &str) -> &str {
    match status {
        "permission" => "awaiting permission",
        "working" => "working",
        "answered" => "waiting for you",
        "idle" => "idle",
        "done" => "finished",
        other => other,
    }
}
