//! Поднять сервер дашборда, если он ещё не отвечает (задача F3).
//!
//! Приложение — «один .app», который человек кладёт в автозапуск вместо
//! launchd-агента (ТЗ §11 M5). Python-часть при этом остаётся установленной
//! отдельно: упаковывать интерпретатор внутрь .app ради локального инструмента
//! незачем, а вот найти уже установленную команду и запустить её — можно.
//!
//! Где ищется команда, по порядку:
//! 1. `CLOUDO_DASH_SERVE` — если задана, выполняется как есть;
//! 2. привычные места установки (`~/.local/bin`, homebrew, каталог разработки).
//!
//! Ничего не найдено — окно всё равно откроется и честно покажет «нет связи»:
//! молча подменять сервер своими силами приложение не должно.

use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Duration;

use crate::tray::DASHBOARD;

/// Сколько ждать ответа сервера, прежде чем считать, что его нет.
const PROBE_TIMEOUT: Duration = Duration::from_millis(700);

/// Запущен ли сервер прямо сейчас.
pub fn is_running() -> bool {
    ureq::get(&format!("{DASHBOARD}/api/health"))
        .timeout(PROBE_TIMEOUT)
        .call()
        .is_ok()
}

/// Найти команду запуска и выполнить её в фоне. Возвращает путь, если запустили.
pub fn start_if_needed() -> Option<PathBuf> {
    if is_running() {
        return None;
    }
    if let Ok(custom) = std::env::var("CLOUDO_DASH_SERVE") {
        let mut parts = custom.split_whitespace();
        let program = parts.next()?;
        let args: Vec<&str> = parts.collect();
        return spawn(PathBuf::from(program), &args);
    }
    let home = std::env::var("HOME").ok()?;
    let candidates = [
        format!("{home}/.local/bin/cdash"),
        "/opt/homebrew/bin/cdash".to_string(),
        "/usr/local/bin/cdash".to_string(),
        format!("{home}/code/cloudo-dash/.venv/bin/cdash"),
    ];
    candidates
        .iter()
        .map(PathBuf::from)
        .find(|path| path.is_file())
        .and_then(|path| spawn(path, &["serve"]))
}

fn spawn(program: PathBuf, args: &[&str]) -> Option<PathBuf> {
    // Вывод уходит в никуда: у сервера свой лог рядом с базой, а держать
    // трубы открытыми ради него незачем — приложение переживёт его перезапуск.
    Command::new(&program)
        .args(args)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok()
        .map(|_| program)
}
