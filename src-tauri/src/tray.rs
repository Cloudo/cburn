//! The menu-bar tray: the spend always in sight (task F2, SPEC §5).
//!
//! The data comes from the same `/api/overview` as the dashboard - the tray counts
//! nothing itself. Polling runs every `POLL` seconds: more often is pointless, the figure
//! in the menu bar is read by eye, not measured.
//!
//! What the icon title shows is chosen in the "menu bar shows" submenu: the burn rate,
//! the cost per hour, the spend of the day and the percentages of the subscription
//! windows - any combination of them, and the choice survives a restart in
//! `~/.local/share/cburn/tray.json`. A red dot before the
//! figure means the spend is above the `thresholds.burn_rate_warn_per_min`
//! threshold from the config; it can be silenced for two hours by the "pause for 2 hours"
//! item - that is silence in the tray only, telegram notifications live apart (D5).
//!
//! The menu speaks the language chosen on the dashboard. The choice stays the browser's
//! (`localStorage`, like the layout), and the tray cannot reach in there: the server
//! mirrors it into `ui.json` next to the database, and the poll reads the language from
//! that file - the phrases themselves come from `dict.json`.

use std::path::PathBuf;
use std::sync::atomic::{AtomicI64, AtomicU32, AtomicU8, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::menu::{CheckMenuItem, Menu, MenuEvent, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Runtime, WebviewUrl, WebviewWindowBuilder};

use crate::dict::{t, tf, Lang};

/// The dashboard address: the same one that opens in a browser. `CBURN_PORT` points the
/// whole application - the window, the tray poll, the health check - at another instance
/// (the demo dataset first of all); the built-in default is the real port.
pub fn dashboard() -> &'static str {
    static URL: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    URL.get_or_init(|| {
        let port = std::env::var("CBURN_PORT")
            .ok()
            .and_then(|value| value.parse::<u16>().ok())
            .unwrap_or(8799);
        format!("http://127.0.0.1:{port}")
    })
}

/// How often to ask for the overview. An instrument in the menu bar is no stopwatch: five
/// seconds is enough for the figure to look alive, and it is four times cheaper than the frontend tick.
const POLL: Duration = Duration::from_secs(5);

/// How long the silence from the "pause for 2 hours" item lasts (SPEC §5).
const PAUSE: Duration = Duration::from_secs(2 * 60 * 60);

/// How many sessions to show in the menu: the three hottest, beyond that the list
/// stops being readable at a glance.
const HOT_SESSIONS: usize = 3;

/// What the icon title may show: the key, the dictionary key of the menu caption and the
/// order in the title. The subscription windows are the same `plan.limits` kinds the
/// dashboard shows.
const METRICS: [(&str, &str); 6] = [
    ("burn", "tray.metric.burn"),
    ("cost", "tray.metric.cost"),
    ("today", "tray.metric.today"),
    ("session", "tray.metric.session"),
    ("weekly_all", "tray.metric.weekly_all"),
    ("weekly_scoped", "tray.metric.weekly_scoped"),
];

/// The default title: the speedometer the tray started with.
const DEFAULT_METRICS: u32 = 1;

/// The language of the menu until the dashboard has been opened at least once: English,
/// the language of everything else outside the interface dictionary.
const DEFAULT_LANG: Lang = Lang::En;

/// What the icon title shows, in which language the menu speaks and until when the alert
/// stays quiet.
struct State {
    /// A bit mask over `METRICS`: which figures go into the icon title.
    metrics: AtomicU32,
    /// The language of the menu: 0 - Russian, 1 - English (the order of `dict.json`).
    lang: AtomicU8,
    /// Unix time until which the red dot is not shown.
    quiet_until: AtomicI64,
}

impl State {
    fn is_quiet(&self) -> bool {
        self.quiet_until.load(Ordering::Relaxed) > unix_now()
    }

    fn shows(&self, index: usize) -> bool {
        self.metrics.load(Ordering::Relaxed) & (1 << index) != 0
    }

    fn lang(&self) -> Lang {
        match self.lang.load(Ordering::Relaxed) {
            0 => Lang::Ru,
            _ => Lang::En,
        }
    }

    fn set_lang(&self, lang: Lang) {
        self.lang
            .store(if lang == Lang::Ru { 0 } else { 1 }, Ordering::Relaxed);
    }
}

fn unix_now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs() as i64)
        .unwrap_or(0)
}

/// Where the tray choice is kept: our own directory next to the database, never `~/.claude`.
fn settings_path() -> Option<PathBuf> {
    state_path("tray.json")
}

fn state_path(name: &str) -> Option<PathBuf> {
    std::env::var("HOME")
        .ok()
        .map(|home| PathBuf::from(home).join(".local/share/cburn").join(name))
}

/// The interface language chosen on the dashboard. The file is written by the server on
/// every switch; until the dashboard has been opened, the menu stays English.
fn load_lang() -> Lang {
    state_path("ui.json")
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .and_then(|value| {
            value
                .get("lang")
                .and_then(|lang| lang.as_str())
                .and_then(Lang::parse)
        })
        .unwrap_or(DEFAULT_LANG)
}

/// Read the chosen metrics. Keys are stored rather than a mask: the file stays readable
/// and survives a change in the `METRICS` order.
fn load_metrics() -> u32 {
    let selected = settings_path()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .and_then(|value| value.get("title").cloned());
    let Some(keys) = selected.as_ref().and_then(|value| value.as_array()) else {
        return DEFAULT_METRICS;
    };
    let mut mask = 0;
    for key in keys.iter().filter_map(|value| value.as_str()) {
        if let Some(index) = METRICS.iter().position(|(id, _)| *id == key) {
            mask |= 1 << index;
        }
    }
    mask
}

fn save_metrics(mask: u32) {
    let keys: Vec<&str> = METRICS
        .iter()
        .enumerate()
        .filter(|(index, _)| mask & (1 << index) != 0)
        .map(|(_, (id, _))| *id)
        .collect();
    let Some(path) = settings_path() else { return };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let payload = serde_json::json!({ "title": keys }).to_string();
    if let Err(error) = std::fs::write(&path, payload) {
        log::warn!("the tray choice was not saved: {error}");
    }
}

/// Menu items refreshed on every poll - and relabelled when the language changes.
struct Items<R: Runtime> {
    burn: MenuItem<R>,
    today: MenuItem<R>,
    sessions: Vec<MenuItem<R>>,
    advice: MenuItem<R>,
    title_menu: Submenu<R>,
    metrics: Vec<CheckMenuItem<R>>,
    pause: MenuItem<R>,
    autostart: MenuItem<R>,
    open: MenuItem<R>,
    quit: MenuItem<R>,
}

pub fn setup<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let lang = load_lang();
    let state = Arc::new(State {
        metrics: AtomicU32::new(load_metrics()),
        lang: AtomicU8::new(0),
        quiet_until: AtomicI64::new(0),
    });
    state.set_lang(lang);

    let burn = MenuItem::with_id(app, "burn", t(lang, "tray.burn.none"), false, None::<&str>)?;
    let today = MenuItem::with_id(
        app,
        "today",
        t(lang, "tray.today.none"),
        false,
        None::<&str>,
    )?;
    let advice = MenuItem::with_id(
        app,
        "advice",
        t(lang, "tray.advice.none"),
        true,
        None::<&str>,
    )?;
    let metrics: Vec<CheckMenuItem<R>> = METRICS
        .iter()
        .enumerate()
        .map(|(index, (id, label))| {
            CheckMenuItem::with_id(
                app,
                format!("metric-{id}"),
                t(lang, label),
                true,
                state.shows(index),
                None::<&str>,
            )
        })
        .collect::<tauri::Result<_>>()?;
    let title_menu = Submenu::with_items(
        app,
        t(lang, "tray.title"),
        true,
        &metrics
            .iter()
            .map(|item| item as &dyn tauri::menu::IsMenuItem<R>)
            .collect::<Vec<_>>(),
    )?;
    let pause = MenuItem::with_id(app, "pause", t(lang, "tray.pause"), true, None::<&str>)?;
    let autostart = MenuItem::with_id(
        app,
        "autostart",
        autostart_label(app, lang),
        true,
        None::<&str>,
    )?;
    let open = MenuItem::with_id(app, "open", t(lang, "tray.open"), true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", t(lang, "tray.quit"), true, None::<&str>)?;
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
        &title_menu,
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
        title_menu,
        metrics,
        pause,
        autostart,
        open,
        quit,
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
        // The language is checked on the same tick as the numbers: the dashboard switch
        // must reach the menu bar without a restart, and reading a small local file
        // costs less than the request that follows it.
        let lang = load_lang();
        if lang != poll_state.lang() {
            poll_state.set_lang(lang);
            relabel(&poll_app, &poll_state, &poll_items, lang);
        }
        match fetch_overview() {
            Ok(overview) => apply(&tray, &poll_state, &poll_items, &overview),
            Err(error) => {
                let _ = tray.set_title(Some("—"));
                let _ = poll_items
                    .burn
                    .set_text(tf(lang, "tray.offline", &[("error", error)]));
            }
        }
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
            let _ = items.autostart.set_text(autostart_label(app, state.lang()));
        }
        "advice" => show_dashboard(app, "#/advice"),
        "pause" => {
            let quiet = state.is_quiet();
            state.quiet_until.store(
                if quiet {
                    0
                } else {
                    unix_now() + PAUSE.as_secs() as i64
                },
                Ordering::Relaxed,
            );
            // The same pause goes to the server too: it silences not only the red dot
            // in the menu bar but also the telegram messages (D5).
            let _ = ureq::post(&format!(
                "{}/api/notify/pause?on={}",
                dashboard(),
                if quiet { "false" } else { "true" }
            ))
            .timeout(Duration::from_secs(3))
            .call();
            let _ = items.pause.set_text(pause_label(state));
        }
        id if id.starts_with("metric-") => {
            let key = &id["metric-".len()..];
            if let Some(index) = METRICS.iter().position(|(name, _)| *name == key) {
                let bit = 1 << index;
                let mask = state.metrics.fetch_xor(bit, Ordering::Relaxed) ^ bit;
                // The choice outlives the process: otherwise every restart would bring
                // the speedometer back over the window a human had picked.
                save_metrics(mask);
                let _ = items.metrics[index].set_checked(mask & bit != 0);
            }
        }
        id => {
            // Sessions are labelled by their own id: `session-0` and so on,
            // while the session id itself sits in the item tooltip.
            if let Some(index) = id
                .strip_prefix("session-")
                .and_then(|n| n.parse::<usize>().ok())
            {
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

/// The caption of the pause item: it also shows whether the silence is on now.
fn pause_label(state: &Arc<State>) -> &'static str {
    let key = if state.is_quiet() {
        "tray.resume"
    } else {
        "tray.pause"
    };
    t(state.lang(), key)
}

/// Say the whole menu again in the new language. Only the captions that do not depend on
/// the numbers are here: the rest are rewritten by the next `apply` anyway.
fn relabel<R: Runtime>(app: &AppHandle<R>, state: &Arc<State>, items: &Items<R>, lang: Lang) {
    for (item, (_, label)) in items.metrics.iter().zip(METRICS.iter()) {
        let _ = item.set_text(t(lang, label));
    }
    let _ = items.title_menu.set_text(t(lang, "tray.title"));
    let _ = items.pause.set_text(pause_label(state));
    let _ = items.autostart.set_text(autostart_label(app, lang));
    let _ = items.open.set_text(t(lang, "tray.open"));
    let _ = items.quit.set_text(t(lang, "tray.quit"));
}

/// Show the dashboard: an open window is raised, otherwise it is created anew.
/// A second launch of the application ends up here as well - instead of its own window.
pub fn show_dashboard<R: Runtime>(app: &AppHandle<R>, hash: &str) {
    let url = format!("{}/{hash}", dashboard());
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
    ureq::get(&format!("{}/api/config", dashboard()))
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
    let response = ureq::get(&format!("{}/api/overview", dashboard()))
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
    let burn = overview
        .pointer("/burn/1m")
        .unwrap_or(&serde_json::Value::Null);
    let output = burn
        .get("output_per_min")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    let cost_hour = burn
        .get("cost_per_hour")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    let tokens = burn
        .get("tokens_per_min")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    // The threshold is asked for once per tick: two calls in a row would cost
    // two extra requests to our own server every five seconds.
    let threshold = warn_threshold();
    let alert = threshold > 0.0 && tokens >= threshold;
    let lang = state.lang();

    let value = METRICS
        .iter()
        .enumerate()
        .filter(|(index, _)| state.shows(*index))
        .map(|(_, (key, _))| metric_text(lang, key, overview, output, cost_hour))
        .collect::<Vec<String>>()
        .join(" · ");
    // The red dot is the only alert in the menu bar: the icon cannot be given
    // a colour, and a dot before the figure is noticeable without hindering reading.
    let title = if value.is_empty() {
        // Everything unticked means the bare icon: the menu is worth something on its own.
        String::new()
    } else if alert && !state.is_quiet() {
        format!("● {value}")
    } else {
        value
    };
    let _ = tray.set_title(if title.is_empty() { None } else { Some(&title) });

    let _ = items.burn.set_text(tf(
        lang,
        "tray.burn",
        &[
            ("tokens", format!("{:.1}", output / 1000.0)),
            ("cost", format!("{cost_hour:.2}")),
        ],
    ));
    let today_cost = overview
        .pointer("/today/cost_usd")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    let today_turns = overview
        .pointer("/today/turns")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let _ = items.today.set_text(tf(
        lang,
        "tray.today",
        &[
            ("turns", today_turns.to_string()),
            ("cost", format!("{today_cost:.2}")),
        ],
    ));

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
                let status = session
                    .get("status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("—");
                let _ = item.set_text(format!(
                    "{id:.8} {project} - {}",
                    status_label(lang, status)
                ));
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
        tf(lang, "tray.advice", &[("ticks", advice_ticks.to_string())])
    } else {
        t(lang, "tray.advice.none").to_string()
    });
}

/// One figure for the icon title. It is kept short: the menu bar has little room,
/// and several chosen metrics stand side by side there.
fn metric_text(
    lang: Lang,
    key: &str,
    overview: &serde_json::Value,
    output: f64,
    cost_hour: f64,
) -> String {
    match key {
        "burn" => tf(
            lang,
            "tray.value.burn",
            &[("value", format!("{:.1}", output / 1000.0))],
        ),
        "cost" => tf(
            lang,
            "tray.value.cost",
            &[("value", format!("{cost_hour:.2}"))],
        ),
        "today" => format!(
            "${:.2}",
            overview
                .pointer("/today/cost_usd")
                .and_then(|value| value.as_f64())
                .unwrap_or(0.0)
        ),
        kind => {
            let window = overview
                .pointer("/plan/limits")
                .and_then(|value| value.as_array())
                .and_then(|rows| {
                    rows.iter()
                        .find(|row| row.get("kind").and_then(|v| v.as_str()) == Some(kind))
                });
            // The scoped window is about a particular model - its name says more than "week".
            let label = window
                .and_then(|row| row.get("model"))
                .and_then(|value| value.as_str())
                .unwrap_or(t(
                    lang,
                    if kind == "session" {
                        "tray.window.session"
                    } else {
                        "tray.window.week"
                    },
                ));
            match window
                .and_then(|row| row.get("percent"))
                .and_then(|value| value.as_f64())
            {
                Some(percent) => format!("{label} {percent:.0}%"),
                // Without an answer from Anthropic a dash is honest, a zero is not.
                None => format!("{label} -"),
            }
        }
    }
}

/// The caption of the autostart item: it also shows the current state.
fn autostart_label<R: Runtime>(app: &AppHandle<R>, lang: Lang) -> &'static str {
    let key = if is_autostart_on(app) {
        "tray.autostart.on"
    } else {
        "tray.autostart.off"
    };
    t(lang, key)
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

/// The session status in words. An unknown one is shown as it came: the server may learn
/// a new status before the tray does, and a raw name is more honest than a blank.
fn status_label(lang: Lang, status: &str) -> &str {
    match status {
        "permission" => t(lang, "tray.status.permission"),
        "working" => t(lang, "tray.status.working"),
        "answered" => t(lang, "tray.status.answered"),
        "idle" => t(lang, "tray.status.idle"),
        "done" => t(lang, "tray.status.done"),
        other => other,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn overview() -> serde_json::Value {
        serde_json::json!({
            "today": {"cost_usd": 12.345},
            "plan": {"limits": [
                {"kind": "session", "model": null, "percent": 69},
                {"kind": "weekly_scoped", "model": "Fable", "percent": 8},
            ]},
        })
    }

    #[test]
    fn title_pieces_stay_short() {
        let data = overview();
        let en = Lang::En;
        assert_eq!(metric_text(en, "burn", &data, 6216.0, 146.09), "6.2K/min");
        assert_eq!(metric_text(en, "cost", &data, 6216.0, 146.09), "$146.09/h");
        assert_eq!(metric_text(en, "today", &data, 0.0, 0.0), "$12.35");
        assert_eq!(metric_text(en, "session", &data, 0.0, 0.0), "5h 69%");
        // The scoped window is named by its model, the shared one by the week.
        assert_eq!(
            metric_text(en, "weekly_scoped", &data, 0.0, 0.0),
            "Fable 8%"
        );
    }

    #[test]
    fn a_missing_window_shows_a_dash_not_a_zero() {
        let en = Lang::En;
        assert_eq!(metric_text(en, "weekly_all", &overview(), 0.0, 0.0), "wk -");
        assert_eq!(
            metric_text(en, "session", &serde_json::json!({}), 0.0, 0.0),
            "5h -"
        );
    }

    #[test]
    fn the_title_speaks_the_chosen_language() {
        // The figures are the same in both languages, the words around them are not.
        let data = overview();
        assert_eq!(
            metric_text(Lang::Ru, "burn", &data, 6216.0, 0.0),
            "6.2K/мин"
        );
        assert_eq!(metric_text(Lang::Ru, "session", &data, 0.0, 0.0), "5ч 69%");
        // A model name is not translated: it comes from Anthropic, not from the dictionary.
        assert_eq!(
            metric_text(Lang::Ru, "weekly_scoped", &data, 0.0, 0.0),
            "Fable 8%"
        );
    }

    #[test]
    fn statuses_are_translated_and_an_unknown_one_is_kept() {
        assert_eq!(status_label(Lang::Ru, "working"), "работает");
        assert_eq!(status_label(Lang::En, "working"), "working");
        assert_eq!(status_label(Lang::Ru, "hibernating"), "hibernating");
    }
}
