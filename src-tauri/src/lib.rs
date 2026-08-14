//! Десктопная обёртка дашборда (веха F, ТЗ §11 M5).
//!
//! Окно грузит тот же самый фронт с `http://127.0.0.1:8799`, который открывается
//! в браузере: фронт для этого не меняется — он с самого начала ходит к бэкенду
//! только по HTTP и WebSocket на localhost (инвариант проекта). Из-за этого
//! страница берётся с сервера, а не из локальных файлов: относительные пути
//! вида `api/overview` должны попадать в наш же сервер, а не в `tauri://`.
//!
//! Трей меню-бара (ТЗ §5) — второй способ смотреть на прибор: цифра расхода
//! всегда на виду, а меню отвечает на вопрос «что сейчас происходит», не
//! требуя открывать окно.

mod server;
mod tray;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
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
            // Сервер поднимается сам, если его нет: .app в автозапуске
            // заменяет launchd-агент, а без сервера окно пустое (F3).
            if let Some(path) = server::start_if_needed() {
                log::info!("запущен сервер дашборда: {}", path.display());
            }
            tray::setup(app.handle())?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("не удалось запустить окно дашборда");
}
