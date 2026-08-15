//! Трей меню-бара: расход всегда на виду (задача F2, ТЗ §5).
//!
//! Данные берутся из того же `/api/overview`, что и дашборд, — трей ничего не
//! считает сам. Опрос идёт раз в `POLL` секунд: чаще незачем, цифра в меню-баре
//! читается глазами, а не измеряется.
//!
//! Что показывается в заголовке иконки: burn rate выходных токенов в минуту,
//! либо стоимость в час — переключается пунктом меню. Красная точка перед
//! цифрой означает, что расход выше порога `thresholds.burn_rate_warn_per_min`
//! из конфига; её можно погасить на два часа пунктом «Пауза на 2 часа» —
//! это тишина именно в трее, уведомления в telegram живут отдельно (D5).

use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Runtime, WebviewUrl, WebviewWindowBuilder};

/// Адрес дашборда: тот же, что открывается в браузере.
pub const DASHBOARD: &str = "http://127.0.0.1:8799";

/// Как часто спрашивать обзор. Прибор в меню-баре — не секундомер: пять секунд
/// достаточно, чтобы цифра выглядела живой, и вчетверо дешевле, чем такт фронта.
const POLL: Duration = Duration::from_secs(5);

/// Сколько длится тишина по пункту «Пауза на 2 часа» (ТЗ §5).
const PAUSE: Duration = Duration::from_secs(2 * 60 * 60);

/// Сколько сессий показывать в меню: три самые горячие, дальше список
/// перестаёт читаться с одного взгляда.
const HOT_SESSIONS: usize = 3;

/// Что показывает заголовок иконки и до какого момента молчит тревога.
struct State {
    /// `true` — деньги в час, `false` — тысячи токенов в минуту.
    show_cost: AtomicBool,
    /// Unix-время, до которого красная точка не показывается.
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

/// Пункты меню, которые обновляются на каждом опросе.
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

    let burn = MenuItem::with_id(app, "burn", "расход: —", false, None::<&str>)?;
    let today = MenuItem::with_id(app, "today", "за сегодня: —", false, None::<&str>)?;
    let advice = MenuItem::with_id(app, "advice", "советов пока нет", true, None::<&str>)?;
    let unit = MenuItem::with_id(app, "unit", "показывать $/ч", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "пауза на 2 часа", true, None::<&str>)?;
    let autostart = MenuItem::with_id(app, "autostart", autostart_label(app), true, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "открыть дашборд", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "выйти", true, None::<&str>)?;
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

    // Опрос живёт своим потоком: обновление меню-бара не должно зависеть от
    // того, открыто ли окно, а окна может не быть вовсе.
    let poll_app = app.clone();
    let poll_state = Arc::clone(&state);
    let poll_items = Arc::clone(&items);
    std::thread::spawn(move || loop {
        match fetch_overview() {
            Ok(overview) => apply(&tray, &poll_state, &poll_items, &overview),
            Err(error) => {
                let _ = tray.set_title(Some("—"));
                let _ = poll_items.burn.set_text(format!("нет связи с cburn serve: {error}"));
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
                "показывать тыс.ток/мин"
            } else {
                "показывать $/ч"
            });
        }
        "pause" => {
            let quiet = state.is_quiet();
            state.quiet_until.store(
                if quiet { 0 } else { unix_now() + PAUSE.as_secs() as i64 },
                Ordering::Relaxed,
            );
            // Та же пауза уходит и на сервер: она гасит не только красную точку
            // в меню-баре, но и сообщения в telegram (D5).
            let _ = ureq::post(&format!(
                "{DASHBOARD}/api/notify/pause?on={}",
                if quiet { "false" } else { "true" }
            ))
            .timeout(Duration::from_secs(3))
            .call();
            let _ = items.pause.set_text(if quiet {
                "пауза на 2 часа"
            } else {
                "снять паузу"
            });
        }
        id => {
            // Сессии подписаны своим идентификатором: `session-0` и так далее,
            // а сам идентификатор сессии лежит в подсказке к пункту.
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

/// Показать дашборд: открытое окно поднимается, иначе создаётся заново.
fn show_dashboard<R: Runtime>(app: &AppHandle<R>, hash: &str) {
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

/// Порог тревоги из конфига дашборда: держать его копию в трее нельзя —
/// человек правит пороги в «Настройках», и трей должен слушаться того же числа.
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
    // Порог спрашивается один раз за такт: два вызова подряд стоили бы
    // двух лишних запросов к своему же серверу каждые пять секунд.
    let threshold = warn_threshold();
    let alert = threshold > 0.0 && tokens >= threshold;

    let value = if state.show_cost.load(Ordering::Relaxed) {
        format!("${cost_hour:.2}/ч")
    } else {
        format!("{:.1}K/мин", output / 1000.0)
    };
    // Красная точка — единственная тревога в меню-баре: цвет иконке задать
    // нельзя, а точка перед цифрой заметна и не мешает читать.
    let title = if alert && !state.is_quiet() {
        format!("● {value}")
    } else {
        value.clone()
    };
    let _ = tray.set_title(Some(&title));

    let _ = items.burn.set_text(format!(
        "расход: {:.1}K ток/мин · ${:.2}/ч",
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
        .set_text(format!("за сегодня: {today_turns} ходов · ${today_cost:.2}"));

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
        format!("советы: {advice_ticks} разбор(а/ов) за сегодня")
    } else {
        "советов пока нет".to_string()
    });
}

/// Подпись пункта автозапуска: она же показывает текущее состояние.
fn autostart_label<R: Runtime>(app: &AppHandle<R>) -> &'static str {
    if is_autostart_on(app) {
        "не запускать при входе"
    } else {
        "запускать при входе"
    }
}

fn is_autostart_on<R: Runtime>(app: &AppHandle<R>) -> bool {
    use tauri_plugin_autostart::ManagerExt;
    app.autolaunch().is_enabled().unwrap_or(false)
}

/// Включить или выключить автозапуск. Ошибку прячем не молча: без прав на
/// LaunchAgents подпись просто не изменится, и это видно в меню.
fn toggle_autostart<R: Runtime>(app: &AppHandle<R>) {
    use tauri_plugin_autostart::ManagerExt;
    let manager = app.autolaunch();
    let result = if is_autostart_on(app) {
        manager.disable()
    } else {
        manager.enable()
    };
    if let Err(error) = result {
        log::warn!("автозапуск не переключился: {error}");
    }
}

fn status_label(status: &str) -> &str {
    match status {
        "permission" => "ждёт разрешения",
        "working" => "работает",
        "answered" => "ждёт вас",
        "idle" => "простаивает",
        "done" => "закончилась",
        other => other,
    }
}
