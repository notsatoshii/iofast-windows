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
use tauri::{AppHandle, Listener, Manager};

use crate::sidecar::SidecarManager;

const DAILY_MS: u64 = 86_400_000;
const WEEKLY_MS: u64 = 604_800_000;
/// How often the thread wakes to look for due runs.
const TICK: Duration = Duration::from_secs(300);
/// A run that fired but never succeeded stays due (see `stamp_on_success`), so it
/// retries — but on this floor, not on every 5-minute tick. An offline laptop
/// must not launch the engine 288 times a day.
const RETRY_MS: u64 = 3_600_000;

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
    // Temp-then-rename, same dir so the replace is atomic on one volume: a torn
    // write here drops every run's Auto setting, and `read_schedules` swallows
    // the parse error as "no schedules at all".
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &path).map_err(|e| e.to_string())
}

/// Re-reads before writing rather than editing the tick's snapshot — a
/// `schedule_set` from the UI can land while a scrape runs. An entry the user
/// cleared meanwhile stays cleared.
fn stamp(app: &AppHandle, dir: &str, field: &str, ms: u64) {
    let mut map = read_schedules(app);
    let Some(entry) = map.get_mut(dir).and_then(Value::as_object_mut) else {
        return;
    };
    entry.insert(field.to_string(), json!(ms));
    let _ = write_schedules(app, &map);
}

#[tauri::command]
pub fn schedule_get(app: AppHandle) -> Result<Map<String, Value>, String> {
    Ok(read_schedules(&app))
}

/// `every` = `daily`|`weekly` to set, `None` to clear. `dir` arrives from the
/// webview, so it is checked to be a library child before it is stored — but the
/// original string is kept as the key so it matches the library rows verbatim
/// (canonicalizing here would desync the UI's per-row lookup).
/// `every` sets/clears the cadence; `proxy` is the optional per-run proxy toggle
/// (default on). `every: Some` sets the cadence and keeps the proxy setting
/// unless `proxy` overrides it. `every: None` with `proxy: Some` flips ONLY the
/// proxy field on an existing entry — a proxy setting without a schedule is
/// inert. `every: None` with `proxy: None` clears the whole schedule.
#[tauri::command]
pub fn schedule_set(
    app: AppHandle,
    dir: String,
    every: Option<String>,
    proxy: Option<bool>,
) -> Result<(), String> {
    crate::store::checked_run_dir(&app, &dir)?;
    let mut map = read_schedules(&app);
    let existing_proxy = map.get(&dir).and_then(|v| v.get("proxy")).and_then(Value::as_bool);
    match every.as_deref() {
        None => match proxy {
            None => {
                map.remove(&dir);
            }
            Some(p) => {
                if let Some(entry) = map.get_mut(&dir).and_then(Value::as_object_mut) {
                    entry.insert("proxy".to_string(), json!(p));
                }
            }
        },
        Some(e @ ("daily" | "weekly")) => {
            // Keep the existing last_ms so re-setting the cadence doesn't reset the
            // clock. A BRAND-NEW schedule stamps `now`, NOT 0: the run has fresh
            // data the moment you enable Auto, so the first auto-scrape waits a full
            // interval. `0` (due-now) would re-scrape fresh data within one tick.
            // (`update` deletes any .crawl/checkpoint.pkl, which would kill Resume
            // — `next_due` guards that for every fire, not just this first one.)
            let last_ms = map
                .get(&dir)
                .and_then(|v| v.get("last_ms"))
                .and_then(Value::as_u64)
                .unwrap_or_else(now_ms);
            let use_proxy = proxy.or(existing_proxy).unwrap_or(true);
            map.insert(dir, json!({ "every": e, "last_ms": last_ms, "proxy": use_proxy }));
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

/// The one run this tick should fire, if any. One fire per tick: `fire_scrape`
/// sets the in-flight flag, so any other due run would only get BUSY.
/// Pure over the map so the skip rules are testable without an AppHandle.
fn next_due(map: &Map<String, Value>, now: u64) -> Option<(String, String, Option<String>, bool)> {
    for (dir, entry) in map.iter() {
        let every = entry.get("every").and_then(Value::as_str).unwrap_or("");
        let last_ms = entry.get("last_ms").and_then(Value::as_u64).unwrap_or(0);
        // Absent proxy field = proxy on (matches pre-toggle schedules verbatim).
        let use_proxy = entry.get("proxy").and_then(Value::as_bool).unwrap_or(true);
        if !due(now, every, last_ms) {
            continue;
        }
        // Fired recently without succeeding: back off instead of retrying every tick.
        let attempt_ms = entry.get("last_attempt_ms").and_then(Value::as_u64).unwrap_or(0);
        if attempt_ms > 0 && now.saturating_sub(attempt_ms) < RETRY_MS {
            continue;
        }
        // Paused mid-crawl. `update` deletes .crawl/checkpoint.pkl (spider.py clears
        // a stale one on any non-resume start), so firing here would destroy the
        // frontier and make the Resume button vanish. Skip WITHOUT stamping: the run
        // stays due and fires once the user resumes or finishes it.
        if dir_is_paused(dir) {
            continue;
        }
        // A run the user deleted: skip it, leave the (harmless) stale entry.
        let manifest = Path::new(dir).join("manifest.json");
        let Some((url, scope)) = manifest.is_file().then(|| read_target(&manifest)).flatten() else {
            continue;
        };
        return Some((dir.clone(), url, scope, use_proxy));
    }
    None
}

/// Same predicate as the library row's `resumable` flag (store::read_run).
fn dir_is_paused(dir: &str) -> bool {
    Path::new(dir).join(".crawl/checkpoint.pkl").is_file()
}

/// `fire_scrape` returns Ok as soon as the JSON-RPC line is written, so its Ok
/// says nothing about the run. Stamp `last_ms` only on the `scrape-done` that
/// carries a real result — otherwise a failed weekly run (offline, 403, sidecar
/// died) waits another full week while the UI calls the docs fresh.
fn stamp_on_success(app: &AppHandle, rid: u64, dir: String) {
    let handle = app.clone();
    app.once("scrape-done", move |event| {
        let payload: Value = serde_json::from_str(event.payload()).unwrap_or(Value::Null);
        if payload.get("rid").and_then(Value::as_u64) == Some(rid) && succeeded(&payload) {
            stamp(&handle, &dir, "last_ms", now_ms());
        }
    });
}

/// The windows' own success test (App.tsx `scrape-done`): a result arrived, the
/// user did not Stop it, and the engine exited clean.
fn succeeded(payload: &Value) -> bool {
    let Some(result) = payload.get("result") else {
        return false;
    };
    result.get("cancelled").and_then(Value::as_bool) != Some(true)
        && result.get("exit_code").and_then(Value::as_i64) == Some(0)
}

pub fn spawn_scheduler(app: AppHandle, sc: SidecarManager) {
    std::thread::spawn(move || loop {
        std::thread::sleep(TICK);
        // One scrape at a time (engine enforces it with BUSY -32000). If one is
        // already running, wait for a later tick rather than stacking a doomed request.
        if crate::scrape_in_flight() {
            continue;
        }
        let now = now_ms();
        let Some((dir, url, scope, use_proxy)) = next_due(&read_schedules(&app), now) else {
            continue;
        };
        if let Ok(rid) = crate::fire_scrape(
            &app,
            &sc,
            &url,
            &dir,
            scope.as_deref(),
            None,
            None,
            true,
            false,
            use_proxy,
        ) {
            stamp(&app, &dir, "last_attempt_ms", now);
            stamp_on_success(&app, rid, dir);
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

    const NOW: u64 = 1_700_000_000_000;

    /// No tempdir crate in this crate's deps; a pid+tag dir is enough for a unit test.
    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("iofast-sched-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("manifest.json"),
            r#"{"info":{"start url":"https://x.com/docs","scope":"/docs"}}"#,
        )
        .unwrap();
        dir
    }

    fn one_due_run(dir: &Path, extra: Value) -> (String, Map<String, Value>) {
        let key = dir.to_string_lossy().into_owned();
        let mut entry = json!({ "every": "daily", "last_ms": NOW - DAILY_MS });
        for (k, v) in extra.as_object().unwrap() {
            entry[k] = v.clone();
        }
        let mut map = Map::new();
        map.insert(key.clone(), entry);
        (key, map)
    }

    #[test]
    fn a_paused_run_is_never_auto_updated() {
        let dir = scratch("paused");
        let (key, map) = one_due_run(&dir, json!({}));
        std::fs::create_dir_all(dir.join(".crawl")).unwrap();
        std::fs::write(dir.join(".crawl/checkpoint.pkl"), b"frontier").unwrap();

        // Due, but resumable: firing an `update` would delete the checkpoint.
        assert!(next_due(&map, NOW).is_none());
        // And it must stay due — no stamp — so it fires once the user resumes/finishes.
        assert_eq!(map[&key]["last_ms"], json!(NOW - DAILY_MS));
        assert!(map[&key].get("last_attempt_ms").is_none());

        // Same run, checkpoint gone: fires, with the manifest's own target.
        std::fs::remove_file(dir.join(".crawl/checkpoint.pkl")).unwrap();
        let (fired, url, scope, use_proxy) =
            next_due(&map, NOW).expect("a due run with no checkpoint fires");
        assert_eq!(fired, key);
        assert_eq!(url, "https://x.com/docs");
        assert_eq!(scope.as_deref(), Some("/docs"));
        // No proxy field on the entry -> proxy on (pre-toggle schedules unchanged).
        assert!(use_proxy);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn proxy_toggle_off_reaches_the_fire() {
        let dir = scratch("noproxy");
        // A due run that opted its scheduled scrape out of the proxy.
        let (_, map) = one_due_run(&dir, json!({ "proxy": false }));
        let (_, _, _, use_proxy) = next_due(&map, NOW).expect("a due run fires");
        assert!(!use_proxy, "proxy:false must flow through to fire_scrape");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_failed_run_retries_but_not_every_tick() {
        let dir = scratch("retry");
        // Attempted, never stamped last_ms: the run failed, so it is still due.
        let (_, map) = one_due_run(&dir, json!({ "last_attempt_ms": NOW - RETRY_MS + 1 }));
        assert!(next_due(&map, NOW).is_none(), "retried inside the backoff floor");
        assert!(next_due(&map, NOW + RETRY_MS).is_some(), "never retried at all");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn only_a_clean_scrape_done_counts_as_success() {
        assert!(succeeded(&json!({ "rid": 1, "result": { "exit_code": 0, "written": 12 } })));
        // Sidecar died / RPC error: no result at all.
        assert!(!succeeded(&json!({ "rid": 1, "error": "sidecar exited before replying" })));
        // User pressed Stop: pages kept, but the docs are not fresh.
        assert!(!succeeded(&json!({ "rid": 1, "result": { "exit_code": 0, "cancelled": true } })));
        assert!(!succeeded(&json!({ "rid": 1, "result": { "exit_code": 3, "written": 0 } })));
    }
}
