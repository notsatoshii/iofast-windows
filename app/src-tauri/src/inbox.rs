//! Phone inbox: links shared from Eric's phone, picked up while the app runs.
//!
//! The poll loop lives here (Rust), next to the auto-scheduler, because the
//! grab it runs is the shell's `fire_grab` — the same path the hotkey panel
//! uses, so a phone link lands in the library and the vault exactly like a
//! pasted one. The HTTP half lives in the engine (`inbox.fetch` / `inbox.done`,
//! engine/src/iofast_engine/inbox.py): this crate has no HTTP/TLS client, and
//! the engine already ships one plus a pytest harness for the server contract.
//!
//! Rules: one link at a time; an id is grabbed at most once per app session; a
//! grab that fails is still reported done (ok=false + the error) so it never
//! loops; a network/server failure leaves links pending for the next poll. If
//! the grab finished but the `done` POST failed, only the POST is retried.
//! The key is read fresh per poll (env IOFAST_INBOX_KEY first, for testing,
//! then the secret store) and only ever sent in the RPC body — never logged.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter};

use crate::sidecar::SidecarManager;

/// Poll cadence while the app is open.
const POLL: Duration = Duration::from_secs(60);
/// After a network failure, look again sooner — a cheap stand-in for a
/// "network came back" hook: the first poll after Wi-Fi returns picks up.
const RETRY_SOON: Duration = Duration::from_secs(20);
/// Let the windows and the sidecar come up before the first poll.
const FIRST_DELAY: Duration = Duration::from_secs(8);
/// How long a phone link waits for a manual grab to finish before the tick gives up.
const BUSY_WAIT: Duration = Duration::from_secs(600);

/// Set by Settings (key saved, toggle flipped): poll now instead of waiting.
static KICK: AtomicBool = AtomicBool::new(false);

pub fn kick() {
    KICK.store(true, Ordering::SeqCst);
}

#[derive(Default)]
struct Status {
    last_poll_ms: u64,
    last_error: Option<String>,
    picked: u64,
}

static STATUS: Mutex<Option<Status>> = Mutex::new(None);

fn set_status(f: impl FnOnce(&mut Status)) {
    let mut guard = STATUS.lock().unwrap();
    f(guard.get_or_insert_with(Status::default));
}

/// Settings reads this: whether the poller is armed and how the last poll went.
/// Never the key itself.
#[tauri::command]
pub fn inbox_status(app: AppHandle) -> Value {
    let guard = STATUS.lock().unwrap();
    let (last_poll_ms, last_error, picked) = guard
        .as_ref()
        .map(|s| (s.last_poll_ms, s.last_error.clone(), s.picked))
        .unwrap_or((0, None, 0));
    json!({
        "enabled": enabled(&app),
        "has_key": crate::store::inbox_key().is_some(),
        "key_from_env": env_key().is_some(),
        "last_poll_ms": last_poll_ms,
        "last_error": last_error,
        "picked": picked,
    })
}

#[tauri::command]
pub fn inbox_poll_now() {
    kick();
}

fn env_key() -> Option<String> {
    std::env::var("IOFAST_INBOX_KEY").ok().filter(|v| !v.trim().is_empty())
}

/// The key the poller uses: env override first (testing), then the store.
pub fn pick_key(env: Option<String>, stored: Option<String>) -> Option<String> {
    env.filter(|v| !v.trim().is_empty())
        .or(stored.filter(|v| !v.trim().is_empty()))
        .map(|v| v.trim().to_string())
}

/// Default on when a key exists; a user who turned it off keeps it off.
pub fn is_enabled(setting: Option<bool>) -> bool {
    setting.unwrap_or(true)
}

fn enabled(app: &AppHandle) -> bool {
    let setting = crate::store::config_get(app.clone())
        .ok()
        .and_then(|c| c["config"]["inbox_enabled"].as_bool());
    is_enabled(setting)
}

/// Dev-only base override for a local fake server; release always uses the
/// engine's default (https://agentos.rest/iofast-inbox), so no env var can
/// point a shipped build's key at another host.
fn base_override() -> Option<String> {
    if cfg!(debug_assertions) {
        std::env::var("IOFAST_INBOX_URL").ok().filter(|v| !v.is_empty())
    } else {
        None
    }
}

/// Port of Panel.tsx `scopePrefix` (and engine urls.scope_prefix):
/// /docs/intro -> /docs/ , /docs/ -> /docs/ , / -> /.
pub fn scope_prefix(path: &str) -> String {
    if path.is_empty() || path == "/" {
        return "/".into();
    }
    if path.ends_with('/') {
        return path.into();
    }
    match path.rfind('/') {
        Some(i) => path[..=i].to_string(),
        None => "/".into(),
    }
}

/// What the panel would submit for this text: an absolute http(s) URL, its host
/// and its default scope. None = not grabbable (reported done with ok=false).
pub fn parse_target(text: &str) -> Option<(String, String, String)> {
    let url = tauri::Url::parse(text.trim()).ok()?;
    if url.scheme() != "http" && url.scheme() != "https" {
        return None;
    }
    let host = url.host_str()?.to_string();
    if host.is_empty() {
        return None;
    }
    Some((url.to_string(), host, scope_prefix(url.path())))
}

#[derive(Debug, PartialEq)]
pub enum Outcome {
    /// Report done with this ok + note.
    Done { ok: bool, note: String, title: String },
    /// Engine was busy with another grab: leave the link pending, stop this tick.
    Busy,
}

/// Map a `grab-done` payload (same shape the panel reads) to a done report.
pub fn outcome(payload: &Value, url: &str) -> Outcome {
    let title = title_of(payload, url);
    if let Some(err) = payload.get("error").and_then(Value::as_str) {
        if err.contains("grab already running") {
            return Outcome::Busy;
        }
        return Outcome::Done { ok: false, note: err.to_string(), title };
    }
    let Some(result) = payload.get("result") else {
        return Outcome::Done { ok: false, note: "the sidecar returned nothing".into(), title };
    };
    if result.get("cancelled").and_then(Value::as_bool) == Some(true) {
        return Outcome::Done { ok: false, note: "cancelled".into(), title };
    }
    let exit = result.get("exit_code").and_then(Value::as_i64).unwrap_or(-1);
    let written = result.get("written").and_then(Value::as_i64).unwrap_or(0);
    if exit != 0 {
        return Outcome::Done {
            ok: false,
            note: format!("engine exit {exit} after {written} page(s)"),
            title,
        };
    }
    let out_dir = result.get("out_dir").and_then(Value::as_str).unwrap_or("");
    let note = match result.get("vault_path").and_then(Value::as_str) {
        Some(p) if !p.is_empty() => p.to_string(),
        _ => match result.get("vault_error").and_then(Value::as_str) {
            Some(e) => format!("{out_dir} (not filed to vault: {e})"),
            None => out_dir.to_string(),
        },
    };
    Outcome::Done { ok: true, note, title }
}

/// A human name for the toast: the manifest's title if the source wrote one,
/// else host + path.
fn title_of(payload: &Value, url: &str) -> String {
    let manifest = &payload["result"]["manifest"];
    let found = manifest["info"]["title"]
        .as_str()
        .or_else(|| manifest["pages"][0]["title"].as_str())
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .map(str::to_string);
    let raw = found.unwrap_or_else(|| {
        tauri::Url::parse(url)
            .ok()
            .and_then(|u| u.host_str().map(|h| format!("{h}{}", u.path().trim_end_matches('/'))))
            .unwrap_or_else(|| url.to_string())
    });
    clip(&raw, 80)
}

fn clip(text: &str, max: usize) -> String {
    let one_line = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if one_line.chars().count() <= max {
        one_line
    } else {
        one_line.chars().take(max - 1).collect::<String>() + "\u{2026}"
    }
}

/// Toast XML is built inside a single-quoted PowerShell string: XML-escape the
/// text, and `&apos;` keeps a `'` in a page title from closing the PS string.
pub fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

/// Same toast mechanism as the grab/scrape completion toasts in main.rs.
fn toast(title: &str, body: &str) {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        let script = format!(
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; \
             [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null; \
             $xml = '<toast><visual><binding template=\"ToastGeneric\"><text>{}</text><text>{}</text></binding></visual></toast>'; \
             $doc = New-Object Windows.Data.Xml.Dom.XmlDocument; $doc.LoadXml($xml); \
             $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('iofast'); \
             $toast = New-Object Windows.UI.Notifications.ToastNotification $doc; \
             $notifier.Show($toast)",
            xml_escape(title),
            xml_escape(body)
        );
        let _ = std::process::Command::new("powershell")
            .args(["-NoProfile", "-NonInteractive", "-Command", &script])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .spawn();
    }
    #[cfg(target_os = "macos")]
    {
        let script = format!(
            "display notification \"{}\" with title \"{}\"",
            body.replace('\\', "\\\\").replace('"', "\\\""),
            title.replace('\\', "\\\\").replace('"', "\\\"")
        );
        let _ = std::process::Command::new("/usr/bin/osascript")
            .args(["-e", &script])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn();
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        let _ = (title, body);
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn rpc_params(key: &str, base: &Option<String>) -> Value {
    let mut p = json!({ "key": key });
    if let Some(b) = base {
        p["base"] = json!(b);
    }
    p
}

/// Per-session memory: ids already grabbed, and done reports not yet accepted
/// by the server (POST failed) — those are re-sent, never re-grabbed.
#[derive(Default)]
pub struct Ledger {
    grabbed: HashSet<String>,
    unacked: HashMap<String, (bool, String)>,
}

impl Ledger {
    /// What to do with a pending id: grab it, only re-send its done, or nothing.
    pub fn plan(&self, id: &str) -> Plan {
        if let Some((ok, note)) = self.unacked.get(id) {
            Plan::ResendDone(*ok, note.clone())
        } else if self.grabbed.contains(id) {
            // Grabbed and acked, yet the server still lists it: never re-grab.
            Plan::ResendDone(false, "already handled by this desktop".into())
        } else {
            Plan::Grab
        }
    }
}

#[derive(Debug, PartialEq)]
pub enum Plan {
    Grab,
    ResendDone(bool, String),
}

enum Tick {
    Ok,
    NetError,
}

/// One poll: fetch pending, then handle each link in order, one at a time.
fn poll_once(app: &AppHandle, sc: &SidecarManager, ledger: &mut Ledger) -> Tick {
    let Some(key) = crate::store::inbox_key() else {
        return Tick::Ok;
    };
    if !enabled(app) || crate::store::library_ready(app).is_err() {
        return Tick::Ok;
    }
    let base = base_override();
    let fetched = tauri::async_runtime::block_on(sc.request(app, "inbox.fetch", rpc_params(&key, &base)));
    set_status(|s| s.last_poll_ms = now_ms());
    let links = match fetched {
        Ok(v) => v["links"].as_array().cloned().unwrap_or_default(),
        Err(e) => {
            // Engine error texts never carry the key (engine/inbox.py).
            log::warn!("phone inbox poll failed: {e}");
            set_status(|s| s.last_error = Some(e.clone()));
            // A rejected key will not fix itself in 20 s; keep the normal cadence.
            return if e.contains("rejected") || e.contains("no phone inbox key") {
                Tick::Ok
            } else {
                Tick::NetError
            };
        }
    };
    set_status(|s| s.last_error = None);
    for link in links {
        let (Some(id), Some(url)) = (link["id"].as_str(), link["url"].as_str()) else {
            continue;
        };
        let (ok, note) = match ledger.plan(id) {
            Plan::ResendDone(ok, note) => (ok, note),
            Plan::Grab => match grab_one(app, sc, url, "phone") {
                None => return Tick::Ok, // engine busy with a manual grab: next poll
                Some(Outcome::Busy) => return Tick::Ok,
                Some(Outcome::Done { ok, note, title }) => {
                    ledger.grabbed.insert(id.to_string());
                    set_status(|s| s.picked += 1);
                    let body = if ok { "Saved".to_string() } else { format!("Failed: {}", clip(&note, 120)) };
                    toast(&format!("From phone: {title}"), &body);
                    let _ = app.emit(
                        "inbox-grabbed",
                        json!({ "id": id, "url": url, "ok": ok, "note": note, "title": title, "origin": "phone" }),
                    );
                    log::info!("phone inbox: link {id} grabbed ok={ok}");
                    (ok, note)
                }
            },
        };
        let mut params = rpc_params(&key, &base);
        params["id"] = json!(id);
        params["ok"] = json!(ok);
        params["note"] = json!(note);
        match tauri::async_runtime::block_on(sc.request(app, "inbox.done", params)) {
            Ok(_) => {
                ledger.unacked.remove(id);
            }
            Err(e) => {
                log::warn!("phone inbox: done for {id} not accepted yet: {e}");
                ledger.unacked.insert(id.to_string(), (ok, note));
                set_status(|s| s.last_error = Some(e.clone()));
                return Tick::NetError;
            }
        }
    }
    Tick::Ok
}

/// Run one grab through the panel's path and wait for its `grab-done`.
/// None = a manual grab is still running after BUSY_WAIT (leave it pending).
fn grab_one(app: &AppHandle, sc: &SidecarManager, url: &str, origin: &str) -> Option<Outcome> {
    let Some((href, host, scope)) = parse_target(url) else {
        return Some(Outcome::Done {
            ok: false,
            note: "not an http(s) link".into(),
            title: clip(url, 80),
        });
    };
    let waited = std::time::Instant::now();
    while crate::grab_in_flight() {
        if waited.elapsed() > BUSY_WAIT {
            return None;
        }
        std::thread::sleep(Duration::from_secs(2));
    }
    let out = match crate::store::library_dir(app.clone(), host, scope) {
        Ok(o) => o,
        Err(e) => return Some(Outcome::Done { ok: false, note: e, title: clip(&href, 80) }),
    };
    let file = crate::store::config_get(app.clone())
        .ok()
        .and_then(|c| c["config"]["file_to_vault"].as_bool())
        .unwrap_or(true);
    let rx = match crate::fire_grab_tracked(app, sc, &href, &out, Some("page"), None, None, file, None, Some(origin)) {
        Ok((_, rx)) => rx,
        Err(e) => return Some(Outcome::Done { ok: false, note: e, title: clip(&href, 80) }),
    };
    let payload = tauri::async_runtime::block_on(rx)
        .unwrap_or_else(|_| json!({ "error": "sidecar exited before replying" }));
    Some(outcome(&payload, &href))
}

/// Hotkey grab: file the page the user is looking at, no panel. Same path as
/// a phone link, so it lands in the library and the vault the same way.
pub fn grab_now(app: AppHandle, sc: SidecarManager, url: String) {
    std::thread::spawn(move || {
        toast("Grabbing", &clip(&url, 120));
        let body = match grab_one(&app, &sc, &url, "hotkey") {
            None | Some(Outcome::Busy) => "Another grab is still running. Try again in a moment.".to_string(),
            Some(Outcome::Done { ok: true, title, .. }) => format!("Saved: {title}"),
            Some(Outcome::Done { ok: false, note, .. }) => format!("Failed: {}", clip(&note, 120)),
        };
        toast("iofast", &body);
        log::info!("hotkey grab {url}: {body}");
    });
}

pub fn spawn_poller(app: AppHandle, sc: SidecarManager) {
    std::thread::spawn(move || {
        let mut ledger = Ledger::default();
        std::thread::sleep(FIRST_DELAY);
        loop {
            let wait = match poll_once(&app, &sc, &mut ledger) {
                Tick::Ok => POLL,
                Tick::NetError => RETRY_SOON,
            };
            KICK.store(false, Ordering::SeqCst);
            let started = std::time::Instant::now();
            while started.elapsed() < wait && !KICK.load(Ordering::SeqCst) {
                std::thread::sleep(Duration::from_secs(1));
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_key_wins_and_blank_keys_do_not_count() {
        assert_eq!(pick_key(Some("env-k".into()), Some("store-k".into())).as_deref(), Some("env-k"));
        assert_eq!(pick_key(Some("  ".into()), Some("store-k".into())).as_deref(), Some("store-k"));
        assert_eq!(pick_key(None, Some(" store-k \n".into())).as_deref(), Some("store-k"));
        assert_eq!(pick_key(None, None), None);
        assert_eq!(pick_key(Some(String::new()), Some(" ".into())), None);
    }

    #[test]
    fn toggle_defaults_on_and_off_sticks() {
        assert!(is_enabled(None));
        assert!(is_enabled(Some(true)));
        assert!(!is_enabled(Some(false)));
    }

    #[test]
    fn scope_matches_the_panel() {
        assert_eq!(scope_prefix("/docs/intro"), "/docs/");
        assert_eq!(scope_prefix("/docs/"), "/docs/");
        assert_eq!(scope_prefix("/"), "/");
        assert_eq!(scope_prefix(""), "/");
        assert_eq!(scope_prefix("/watch"), "/");
    }

    #[test]
    fn only_http_links_are_grabbable() {
        let (href, host, scope) = parse_target(" https://www.youtube.com/watch?v=abc ").unwrap();
        assert_eq!(href, "https://www.youtube.com/watch?v=abc");
        assert_eq!(host, "www.youtube.com");
        assert_eq!(scope, "/");
        assert!(parse_target("javascript:alert(1)").is_none());
        assert!(parse_target("file:///C:/x").is_none());
        assert!(parse_target("just some text").is_none());
    }

    #[test]
    fn grab_results_map_to_done_reports() {
        let url = "https://example.com/a/post";
        let ok = json!({ "rid": 1, "result": { "exit_code": 0, "written": 1, "out_dir": "C:\\lib\\example-com-a",
            "vault_path": "C:\\vault\\Inbox\\post.md", "manifest": { "info": { "title": "A post" } } } });
        assert_eq!(
            outcome(&ok, url),
            Outcome::Done { ok: true, note: "C:\\vault\\Inbox\\post.md".into(), title: "A post".into() }
        );
        let no_vault = json!({ "result": { "exit_code": 0, "out_dir": "C:\\lib\\x", "manifest": {} } });
        match outcome(&no_vault, url) {
            Outcome::Done { ok, note, title } => {
                assert!(ok);
                assert_eq!(note, "C:\\lib\\x");
                assert_eq!(title, "example.com/a/post");
            }
            other => panic!("{other:?}"),
        }
        let failed = json!({ "error": "RuntimeError: 403" });
        assert!(matches!(outcome(&failed, url), Outcome::Done { ok: false, .. }));
        let exit = json!({ "result": { "exit_code": 1, "written": 0 } });
        match outcome(&exit, url) {
            Outcome::Done { ok, note, .. } => {
                assert!(!ok);
                assert!(note.contains("exit 1"));
            }
            other => panic!("{other:?}"),
        }
        let cancelled = json!({ "result": { "exit_code": 0, "cancelled": true } });
        assert!(matches!(outcome(&cancelled, url), Outcome::Done { ok: false, .. }));
        // A manual grab held the engine: transient, not a failure of the link.
        let busy = json!({ "error": "grab already running" });
        assert_eq!(outcome(&busy, url), Outcome::Busy);
    }

    #[test]
    fn an_id_is_grabbed_once_and_a_lost_done_is_only_resent() {
        let mut ledger = Ledger::default();
        assert_eq!(ledger.plan("a"), Plan::Grab);
        ledger.grabbed.insert("a".into());
        ledger.unacked.insert("a".into(), (true, "C:\\v\\a.md".into()));
        assert_eq!(ledger.plan("a"), Plan::ResendDone(true, "C:\\v\\a.md".into()));
        ledger.unacked.remove("a");
        assert!(matches!(ledger.plan("a"), Plan::ResendDone(false, _)));
        assert_eq!(ledger.plan("b"), Plan::Grab);
    }

    #[test]
    fn toast_text_cannot_break_out_of_the_powershell_string() {
        let t = xml_escape("Tom's <b>\"x\"</b> & co");
        assert!(!t.contains('\''));
        assert!(!t.contains('<'));
        assert_eq!(t, "Tom&apos;s &lt;b&gt;&quot;x&quot;&lt;/b&gt; &amp; co");
    }

    #[test]
    fn clip_keeps_one_short_line() {
        assert_eq!(clip("a\n b\tc", 80), "a b c");
        let long = "x".repeat(200);
        assert_eq!(clip(&long, 80).chars().count(), 80);
    }
}
