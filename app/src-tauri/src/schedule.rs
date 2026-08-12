//! Per-run auto re-scrape. A run can be set to re-scrape `daily` or `weekly`;
//! one background thread wakes every few minutes and fires the due ones through
//! the same `fire_scrape` helper the manual Re-scrape button uses.
//!
//! ponytail: this only runs while the app is running — it is a resident tray
//! app, not a launchd/cron job. A machine asleep past a run's interval simply
//! re-scrapes on the next tick after it wakes. Wire a LaunchAgent if true
//! background scheduling is ever wanted.

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{json, Map, Value};
use tauri::{AppHandle, Manager};

use crate::sidecar::SidecarManager;

const DAILY_MS: u64 = 86_400_000;
const WEEKLY_MS: u64 = 604_800_000;
/// How often the thread wakes to look for due runs.
const TICK: Duration = Duration::from_secs(300);

/// Sits beside config.json, same dir. Shape: `{ "<run dir>": {"every","last_ms"} }`.
fn schedules_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_config_dir()
        .map_err(|e| e.to_string())?
        .join("schedules.json"))
}

fn read_schedules(app: &AppHandle) -> Map<String, Value> {
    schedules_path(app)
        .ok()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

fn write_schedules(app: &AppHandle, map: &Map<String, Value>) -> Result<(), String> {
    let path = schedules_path(app)?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(&Value::Object(map.clone())).map_err(|e| e.to_string())?;
    std::fs::write(path, text).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn schedule_get(app: AppHandle) -> Result<Map<String, Value>, String> {
    Ok(read_schedules(&app))
}

/// `every` = `daily`|`weekly` to set, `None` to clear. `dir` arrives from the
/// webview, so it is checked to be a library child before it is stored — but the
/// original string is kept as the key so it matches the library rows verbatim
/// (canonicalizing here would desync the UI's per-row lookup).
#[tauri::command]
pub fn schedule_set(app: AppHandle, dir: String, every: Option<String>) -> Result<(), String> {
    crate::store::checked_run_dir(&app, &dir)?;
    let mut map = read_schedules(&app);
    match every.as_deref() {
        None => {
            map.remove(&dir);
        }
        Some(e @ ("daily" | "weekly")) => {
            // Keep the existing last_ms so re-setting the cadence doesn't reset the
            // clock. A BRAND-NEW schedule stamps `now`, NOT 0: the run has fresh
            // data the moment you enable Auto, so the first auto-scrape waits a full
            // interval. `0` (due-now) would fire an `update` within one tick, and
            // update deletes any .crawl/checkpoint.pkl — silently killing Resume.
            let last_ms = map
                .get(&dir)
                .and_then(|v| v.get("last_ms"))
                .and_then(Value::as_u64)
                .unwrap_or_else(now_ms);
            map.insert(dir, json!({ "every": e, "last_ms": last_ms }));
        }
        Some(other) => return Err(format!("unknown schedule {other}")),
    }
    write_schedules(&app, &map)
}

/// A never-run entry (`last_ms` 0) is due at once. Strictly `>=` so the interval
/// is the minimum gap, not a hard fence a slow tick would overshoot forever.
pub fn due(now_ms: u64, every: &str, last_ms: u64) -> bool {
    let interval = match every {
        "daily" => DAILY_MS,
        "weekly" => WEEKLY_MS,
        _ => return false,
    };
    now_ms.saturating_sub(last_ms) >= interval
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// The run's own manifest is the source of truth for what to re-scrape.
fn read_target(manifest: &Path) -> Option<(String, Option<String>)> {
    let value: Value = serde_json::from_str(&std::fs::read_to_string(manifest).ok()?).ok()?;
    let info = value.get("info")?;
    let url = info.get("start url")?.as_str()?.to_string();
    let scope = info.get("scope").and_then(Value::as_str).map(str::to_string);
    Some((url, scope))
}

pub fn spawn_scheduler(app: AppHandle, sc: SidecarManager) {
    std::thread::spawn(move || loop {
        std::thread::sleep(TICK);
        // One scrape at a time (engine enforces it with BUSY -32000). If one is
        // already running, wait for a later tick rather than stacking a doomed request.
        if crate::scrape_in_flight() {
            continue;
        }
        let map = read_schedules(&app);
        let now = now_ms();
        for (dir, entry) in map.iter() {
            let every = entry.get("every").and_then(Value::as_str).unwrap_or("");
            let last_ms = entry.get("last_ms").and_then(Value::as_u64).unwrap_or(0);
            if !due(now, every, last_ms) {
                continue;
            }
            // A run the user deleted: skip it, leave the (harmless) stale entry.
            let manifest = Path::new(dir).join("manifest.json");
            let Some((url, scope)) = manifest.is_file().then(|| read_target(&manifest)).flatten()
            else {
                continue;
            };
            if crate::fire_scrape(&app, &sc, &url, dir, scope.as_deref(), None, None, true, false).is_ok() {
                let mut next = map.clone();
                next.insert(dir.clone(), json!({ "every": every, "last_ms": now }));
                let _ = write_schedules(&app, &next);
            }
            // One fire per tick: fire_scrape just set the in-flight flag, so any
            // other due run would only get BUSY. Next tick catches it.
            break;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn due_waits_a_full_interval() {
        let now = 1_700_000_000_000u64;
        // Just scheduled: not due (must-fail case).
        assert!(!due(now, "daily", now));
        // A second short of a day: still not due.
        assert!(!due(now, "daily", now - DAILY_MS + 1000));
        // Exactly a day elapsed: due.
        assert!(due(now, "daily", now - DAILY_MS));
        // Weekly needs the longer gap — a day is not enough, a week is.
        assert!(!due(now, "weekly", now - DAILY_MS));
        assert!(due(now, "weekly", now - WEEKLY_MS));
        // Never run (last_ms 0) is due immediately.
        assert!(due(now, "daily", 0));
        // Unknown cadence never fires.
        assert!(!due(now, "monthly", 0));
    }
}
