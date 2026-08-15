//! Bring the dashboard server up if it does not answer yet (task F3).
//!
//! The application is "one .app" a human puts into autostart instead of the
//! launchd agent (TZ §11 M5). The Python part stays installed separately:
//! packing an interpreter inside the .app for a local tool is pointless,
//! while finding an already installed command and running it is not.
//!
//! Where the command is looked for, in order:
//! 1. `CBURN_SERVE` - if set, it is executed as it is;
//! 2. the usual install locations (`~/.local/bin`, homebrew, the development directory).
//!
//! Nothing found - the window still opens and honestly shows "offline":
//! the application must not quietly stand in for the server on its own.

use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Duration;

use crate::tray::DASHBOARD;

/// How long to wait for the server answer before deciding it is absent.
const PROBE_TIMEOUT: Duration = Duration::from_millis(700);

/// Whether the server is running right now.
pub fn is_running() -> bool {
    ureq::get(&format!("{DASHBOARD}/api/health"))
        .timeout(PROBE_TIMEOUT)
        .call()
        .is_ok()
}

/// Find the start command and run it in the background. Returns the path if we started it.
pub fn start_if_needed() -> Option<PathBuf> {
    if is_running() {
        return None;
    }
    if let Ok(custom) = std::env::var("CBURN_SERVE") {
        let mut parts = custom.split_whitespace();
        let program = parts.next()?;
        let args: Vec<&str> = parts.collect();
        return spawn(PathBuf::from(program), &args);
    }
    let home = std::env::var("HOME").ok()?;
    let candidates = [
        format!("{home}/.local/bin/cburn"),
        "/opt/homebrew/bin/cburn".to_string(),
        "/usr/local/bin/cburn".to_string(),
        format!("{home}/code/cburn/.venv/bin/cburn"),
        format!("{home}/code/cloudo-dash/.venv/bin/cburn"), // the directory of the former name
    ];
    candidates
        .iter()
        .map(PathBuf::from)
        .find(|path| path.is_file())
        .and_then(|path| spawn(path, &["serve"]))
}

fn spawn(program: PathBuf, args: &[&str]) -> Option<PathBuf> {
    // The output goes nowhere: the server has its own log next to the database, and
    // keeping pipes open for it is pointless - the application survives its restart.
    Command::new(&program)
        .args(args)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok()
        .map(|_| program)
}
