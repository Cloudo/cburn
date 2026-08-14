//! Десктопная обёртка дашборда (веха F, ТЗ §11 M5).
//!
//! Окно грузит тот же самый фронт с `http://127.0.0.1:8799`, который открывается
//! в браузере: фронт для этого не меняется — он с самого начала ходит к бэкенду
//! только по HTTP и WebSocket на localhost (инвариант проекта). Из-за этого
//! страница берётся с сервера, а не из локальных файлов: относительные пути
//! вида `api/overview` должны попадать в наш же сервер, а не в `tauri://`.

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("не удалось запустить окно дашборда");
}
