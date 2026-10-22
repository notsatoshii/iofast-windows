#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod about;
mod autostart;
mod schedule;
mod sidecar;
mod store;

use std::sync::atomic::{AtomicBool, Ordering};

use serde_json::{json, Value};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{
    AppHandle, Emitter, Listener, LogicalSize, Manager, PhysicalPosition, State, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri_plugin_opener::OpenerExt;

use sidecar::SidecarManager;

// Hotkey modifiers are computed per-platform in default_hotkey() because
// bitflags `|` is not const on keyboard-types 0.7 / bitflags 2.x.

/// Panel geometry. Width is fixed; height follows the content (`panel_resize`).
const PANEL_WIDTH: f64 = 680.0;
const PANEL_TOP_FRACTION: f64 = 0.18;

// ── Configurable hotkey ────────────────────────────────────────────────

/// Default hotkey per platform. Used when no saved preference exists.
fn default_hotkey() -> Shortcut {
    #[cfg(target_os = "macos")]
    {
        Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyK)
    }
    #[cfg(not(target_os = "macos"))]
    {
        Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space)
    }
}

/// Human-readable label for a shortcut, e.g. "Ctrl+Shift+Space".
fn shortcut_label(s: &Shortcut) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mods = s.mods;
    if mods.contains(Modifiers::CONTROL) {
        parts.push("Ctrl".into());
    }
    if mods.contains(Modifiers::ALT) {
        parts.push("Alt".into());
    }
    if mods.contains(Modifiers::SHIFT) {
        parts.push("Shift".into());
    }
    if mods.contains(Modifiers::SUPER) {
        #[cfg(target_os = "macos")]
        parts.push("Cmd".into());
        #[cfg(not(target_os = "macos"))]
        parts.push("Win".into());
    }
    parts.push(format!("{:?}", s.key));
    parts.join("+")
}

/// Parse a human-readable shortcut string like "Ctrl+Shift+Space" into a
/// `Shortcut`. Returns `None` on any parse failure.
fn parse_shortcut(s: &str) -> Option<Shortcut> {
    let parts: Vec<&str> = s.split('+').map(str::trim).collect();
    if parts.is_empty() || parts.len() > 5 {
        return None;
    }
    let (mod_parts, key_part) = parts.split_at(parts.len() - 1);
    let key_str = *key_part.first()?;

    let code = match key_str.to_lowercase().as_str() {
        "space" => Code::Space,
        "enter" | "return" => Code::Enter,
        "escape" | "esc" => Code::Escape,
        "tab" => Code::Tab,
        "backspace" => Code::Backspace,
        "delete" => Code::Delete,
        "up" => Code::ArrowUp,
        "down" => Code::ArrowDown,
        "left" => Code::ArrowLeft,
        "right" => Code::ArrowRight,
        "home" => Code::Home,
        "end" => Code::End,
        "pageup" => Code::PageUp,
        "pagedown" => Code::PageDown,
        // F1-F12
        "f1" => Code::F1,
        "f2" => Code::F2,
        "f3" => Code::F3,
        "f4" => Code::F4,
        "f5" => Code::F5,
        "f6" => Code::F6,
        "f7" => Code::F7,
        "f8" => Code::F8,
        "f9" => Code::F9,
        "f10" => Code::F10,
        "f11" => Code::F11,
        "f12" => Code::F12,
        // Single letters
        other if other.len() == 1 => {
            let ch = other.chars().next()?.to_ascii_uppercase();
            if !ch.is_ascii_alphabetic() {
                return None;
            }
            // Map A-Z to Code::KeyA..Code::KeyZ
            match ch {
                'A' => Code::KeyA, 'B' => Code::KeyB, 'C' => Code::KeyC,
                'D' => Code::KeyD, 'E' => Code::KeyE, 'F' => Code::KeyF,
                'G' => Code::KeyG, 'H' => Code::KeyH, 'I' => Code::KeyI,
                'J' => Code::KeyJ, 'K' => Code::KeyK, 'L' => Code::KeyL,
                'M' => Code::KeyM, 'N' => Code::KeyN, 'O' => Code::KeyO,
                'P' => Code::KeyP, 'Q' => Code::KeyQ, 'R' => Code::KeyR,
                'S' => Code::KeyS, 'T' => Code::KeyT, 'U' => Code::KeyU,
                'V' => Code::KeyV, 'W' => Code::KeyW, 'X' => Code::KeyX,
                'Y' => Code::KeyY, 'Z' => Code::KeyZ,
                _ => return None,
            }
        }
        // Digits
        other if other.len() == 1 && other.chars().next()?.is_ascii_digit() => {
            match other {
                "0" => Code::Digit0, "1" => Code::Digit1, "2" => Code::Digit2,
                "3" => Code::Digit3, "4" => Code::Digit4, "5" => Code::Digit5,
                "6" => Code::Digit6, "7" => Code::Digit7, "8" => Code::Digit8,
                "9" => Code::Digit9,
                _ => return None,
            }
        }
        _ => return None,
    };

    let mut mods = Modifiers::empty();
    for m in mod_parts {
        match m.to_lowercase().as_str() {
            "ctrl" | "control" => mods |= Modifiers::CONTROL,
            "alt" | "option" => mods |= Modifiers::ALT,
            "shift" => mods |= Modifiers::SHIFT,
            "super" | "cmd" | "win" | "meta" => mods |= Modifiers::SUPER,
            _ => return None,
        }
    }

    Some(Shortcut::new(Some(mods), code))
}

fn hotkey_config_path(app: &AppHandle) -> Result<std::path::PathBuf, String> {
    Ok(app
        .path()
        .app_config_dir()
        .map_err(|e| e.to_string())?
        .join("hotkey.json"))
}

fn read_saved_hotkey(app: &AppHandle) -> Option<String> {
    let text = std::fs::read_to_string(hotkey_config_path(app).ok()?).ok()?;
    serde_json::from_str::<String>(&text).ok()
}

fn save_hotkey(app: &AppHandle, label: &str) -> Result<(), String> {
    let path = hotkey_config_path(app)?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let json = serde_json::to_string(label).map_err(|e| e.to_string())?;
    std::fs::write(path, json).map_err(|e| e.to_string())
}

#[tauri::command]
fn hotkey_get(app: AppHandle) -> Value {
    let saved = read_saved_hotkey(&app);
    let current = saved.as_deref().map(|s| s.to_string());
    let default_label = shortcut_label(&default_hotkey());
    json!({
        "current": current.clone().unwrap_or_else(|| default_label.clone()),
        "default": default_label,
        "is_default": current.is_none(),
    })
}

#[tauri::command]
fn hotkey_set(app: AppHandle, shortcut: String) -> Result<Value, String> {
    let parsed = parse_shortcut(&shortcut)
        .ok_or_else(|| format!("invalid shortcut format: {shortcut}"))?;

    // Unregister the old hotkey first (ignore errors — may not be registered).
    let _ = app.global_shortcut().unregister_all();

    // Try to register the new one.
    if let Err(e) = app.global_shortcut().register(parsed) {
        // Re-register the previous working hotkey so the user isn't left
        // without any way to open the panel.
        let fallback = read_saved_hotkey(&app)
            .and_then(|s| parse_shortcut(&s))
            .unwrap_or_else(default_hotkey);
        let _ = app.global_shortcut().register(fallback);
        return Ok(json!({
            "ok": false,
            "error": format!("could not register: {e}"),
        }));
    }

    // Persist only after successful registration.
    let label = shortcut_label(&parsed);
    save_hotkey(&app, &label)?;

    Ok(json!({ "ok": true, "label": label }))
}

#[tauri::command]
async fn sidecar_initialize(app: AppHandle, sc: State<'_, SidecarManager>) -> Result<Value, String> {
    sc.request(&app, "initialize", json!({})).await
}

#[tauri::command]
async fn sidecar_map(
    app: AppHandle,
    sc: State<'_, SidecarManager>,
    url: String,
    scope: Option<String>,
) -> Result<Value, String> {
    sc.request(&app, "map", json!({ "url": url, "scope": scope })).await
}

/// docs/RPC.md `estimate`: static-only, 800ms server budget. The panel races it
/// against its own 1s timer and never blocks Enter on it.
#[tauri::command]
async fn sidecar_estimate(
    app: AppHandle,
    sc: State<'_, SidecarManager>,
    url: String,
) -> Result<Value, String> {
    sc.request(&app, "estimate", json!({ "url": url })).await
}

/// True from the moment a `scrape` request is written until its reply lands.
///
/// ponytail: one global flag, not per-run — the engine is the real one-at-a-time
/// authority (a second scrape gets BUSY -32000). This only lets the scheduler
/// skip a tick instead of firing a request it knows will be refused.
static SCRAPE_IN_FLIGHT: AtomicBool = AtomicBool::new(false);

pub(crate) fn scrape_in_flight() -> bool {
    SCRAPE_IN_FLIGHT.load(Ordering::SeqCst)
}

/// Fire one `scrape` and wire its lifecycle events, returning the request id
/// immediately — the result arrives minutes later on `scrape-done`. The manual
/// button (via `sidecar_scrape_start`) and the auto-scheduler both go through
/// here, so the `scrape-started`/`scrape-done` contract stays identical.
pub(crate) fn fire_scrape(
    app: &AppHandle,
    sc: &SidecarManager,
    url: &str,
    out: &str,
    scope: Option<&str>,
    depth: Option<i64>,
    max_pages: Option<i64>,
    update: bool,
    resume: bool,
    use_proxy: bool,
    interactive: bool,
) -> Result<u64, String> {
    let mut params = json!({ "url": url, "out": out, "scope": scope });
    // Omitted rather than null: docs/RPC.md types these, and the engine defaults them.
    if let Some(d) = depth {
        params["depth"] = json!(d);
    }
    if let Some(m) = max_pages {
        params["max_pages"] = json!(m);
    }
    // update and resume are mutually exclusive; the engine rejects both at once.
    if update {
        params["update"] = json!(true);
    }
    if resume {
        params["resume"] = json!(true);
    }
    // Only when the toggle is on AND a human is at the machine (manual runs).
    if interactive {
        params["interactive"] = json!(true);
    }
    // docs/RPC.md "API-key handoff": read from the keychain here, send in the
    // request body, per run. Never argv, never the sidecar's spawn environment.
    let mut env = store::scrape_env();
    // A scheduled run with its proxy toggle off drops IOFAST_PROXY: a dead proxy
    // would otherwise silently fail every unattended weekly re-scrape. Manual
    // runs always pass use_proxy=true, so the Settings proxy still applies there.
    if !use_proxy {
        env.remove("IOFAST_PROXY");
    }
    if !env.is_empty() {
        params["env"] = Value::Object(env);
    }
    let (rid, rx) = sc.start_request(app, "scrape", params)?;
    SCRAPE_IN_FLIGHT.store(true, Ordering::SeqCst);
    // Both windows track the run from this, so a scrape started in the quick
    // panel still shows its progress strip in the main window.
    let _ = app.emit("scrape-started", json!({ "rid": rid, "url": url, "out": out, "update": update }));
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let payload = match rx.await {
            Ok(Ok(result)) => json!({ "rid": rid, "result": result }),
            Ok(Err(message)) => json!({ "rid": rid, "error": message }),
            Err(_) => json!({ "rid": rid, "error": "sidecar exited before replying" }),
        };
        SCRAPE_IN_FLIGHT.store(false, Ordering::SeqCst);
        let _ = tauri::Emitter::emit(&app, "scrape-done", payload.clone());
        // Toast notification on completion so the user knows without watching.
        #[cfg(target_os = "windows")]
        {
            let (title, body) = if payload.get("error").is_some() {
                ("iofast", "Scrape failed — check the library for details.")
            } else {
                ("iofast", "Scrape complete.")
            };
            // PowerShell toast via BurntToast-free inline XML. Fire-and-forget;
            // a missing toast module is never fatal.
            let script = format!(
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; \
                 [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null; \
                 $xml = '<toast><visual><binding template=\"ToastGeneric\"><text>{}</text><text>{}</text></binding></visual></toast>'; \
                 $doc = New-Object Windows.Data.Xml.Dom.XmlDocument; $doc.LoadXml($xml); \
                 $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('iofast'); \
                 $toast = New-Object Windows.UI.Notifications.ToastNotification $doc; \
                 $notifier.Show($toast)",
                title, body
            );
            {
                use std::os::windows::process::CommandExt;
                const CREATE_NO_WINDOW: u32 = 0x08000000;
                let _ = std::process::Command::new("powershell")
                    .args(["-NoProfile", "-NonInteractive", "-Command", &script])
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .creation_flags(CREATE_NO_WINDOW)
                    .spawn();
            }
        }
        #[cfg(target_os = "macos")]
        {
            let (title, body) = if payload.get("error").is_some() {
                ("iofast", "Scrape failed — check the library for details.")
            } else {
                ("iofast", "Scrape complete.")
            };
            let script = format!(
                "display notification \"{}\" with title \"{}\"",
                body.replace('"', "\\\""),
                title.replace('"', "\\\"")
            );
            let _ = std::process::Command::new("/usr/bin/osascript")
                .args(["-e", &script])
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .spawn();
        }
    });
    Ok(rid)
}

/// Returns the request id immediately; the run's result arrives later as a
/// `scrape-done` event. A scrape can take minutes — nothing may await it here.
#[tauri::command]
async fn sidecar_scrape_start(
    app: AppHandle,
    sc: State<'_, SidecarManager>,
    url: String,
    out: String,
    scope: Option<String>,
    depth: Option<i64>,
    max_pages: Option<i64>,
    update: Option<bool>,
    resume: Option<bool>,
    interactive: Option<bool>,
) -> Result<u64, String> {
    fire_scrape(
        &app,
        &sc,
        &url,
        &out,
        scope.as_deref(),
        depth,
        max_pages,
        update == Some(true),
        resume == Some(true),
        true, // manual runs always honour the Settings proxy
        interactive == Some(true),
    )
}

/// Ingest mode 3 (API push): ask an LLM the user's `prompt` over one run's
/// scraped corpus, return the digest. `dir` arrives from the webview, so it is
/// checked to be a library child here (like `open_in_agent`/`mcp_index`) before
/// its path reaches the sidecar. The OPENROUTER_API_KEY / IOFAST_RESCUE_MODEL
/// ride in the request body from the keychain — same handoff as `scrape`, never
/// argv. `model` is not a secret; it is a plain param the engine defaults from
/// IOFAST_RESCUE_MODEL when omitted. Awaited: one LLM call, not a long crawl.
#[tauri::command]
async fn sidecar_push(
    app: AppHandle,
    sc: State<'_, SidecarManager>,
    dir: String,
    prompt: String,
    model: Option<String>,
) -> Result<Value, String> {
    let run_dir = store::checked_run_dir(&app, &dir)?;
    let mut params = json!({ "dir": run_dir.to_string_lossy(), "prompt": prompt });
    if let Some(m) = model {
        if !m.trim().is_empty() {
            params["model"] = json!(m);
        }
    }
    let env = store::scrape_env();
    if !env.is_empty() {
        params["env"] = Value::Object(env);
    }
    sc.request(&app, "push", params).await
}

/// docs/RPC.md `browser_status`: out-of-band component state, answered fast.
#[tauri::command]
async fn sidecar_browser_status(
    app: AppHandle,
    sc: State<'_, SidecarManager>,
) -> Result<Value, String> {
    sc.request(&app, "browser_status", json!({})).await
}

/// docs/RPC.md `install_browsers`: minutes-long (~770MB). Progress arrives as
/// `browser_install` engine-events; awaiting the reply here is fine — the
/// invoke promise resolves when the install finishes (same shape as `push`).
#[tauri::command]
async fn sidecar_install_browsers(
    app: AppHandle,
    sc: State<'_, SidecarManager>,
) -> Result<Value, String> {
    sc.request(&app, "install_browsers", json!({})).await
}

#[tauri::command]
async fn sidecar_cancel(
    app: AppHandle,
    sc: State<'_, SidecarManager>,
    rid: Option<u64>,
) -> Result<Value, String> {
    sc.request(&app, "cancel", json!({ "rid": rid })).await
}

#[tauri::command]
fn open_path(app: AppHandle, path: String) -> Result<(), String> {
    app.opener()
        .open_path(path, None::<&str>)
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn panel_hide(app: AppHandle) {
    hide_panel(&app);
}

/// The panel window is transparent and top-anchored, so it tracks its content
/// height — an oversized window would swallow clicks meant for what's behind it.
#[tauri::command]
fn panel_resize(app: AppHandle, height: f64) {
    if let Some(panel) = app.get_webview_window("panel") {
        let _ = panel.set_size(LogicalSize::new(PANEL_WIDTH, height.clamp(96.0, 720.0)));
    }
}

/// Clipboard read. macOS uses `pbpaste`; Windows uses the Win32 clipboard API
/// directly (no crate needed for plain text). Both return None on empty or error.
fn clipboard_text() -> Option<String> {
    #[cfg(target_os = "macos")]
    {
        let out = std::process::Command::new("/usr/bin/pbpaste").output().ok()?;
        let text = String::from_utf8(out.stdout).ok()?;
        return (!text.trim().is_empty()).then_some(text);
    }
    #[cfg(target_os = "windows")]
    {
        use windows_sys::Win32::System::DataExchange::{
            CloseClipboard, GetClipboardData, IsClipboardFormatAvailable, OpenClipboard,
        };
        use windows_sys::Win32::System::Memory::{GlobalLock, GlobalUnlock};
        // CF_UNICODETEXT = 13; not re-exported as a const in windows-sys 0.59 DataExchange feature.
        const CF_UNICODETEXT: u32 = 13;

        unsafe {
            if IsClipboardFormatAvailable(CF_UNICODETEXT) == 0 {
                return None;
            }
            if OpenClipboard(std::ptr::null_mut()) == 0 {
                return None;
            }
            let handle = GetClipboardData(CF_UNICODETEXT as u32);
            if handle.is_null() {
                let _ = CloseClipboard();
                return None;
            }
            let ptr = GlobalLock(handle) as *const u16;
            if ptr.is_null() {
                let _ = CloseClipboard();
                return None;
            }
            let mut len = 0;
            while *ptr.add(len) != 0 {
                len += 1;
            }
            let slice = std::slice::from_raw_parts(ptr, len);
            let text = String::from_utf16_lossy(slice);
            let _ = GlobalUnlock(handle);
            let _ = CloseClipboard();
            return (!text.trim().is_empty()).then_some(text);
        }
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    None
}

fn place_panel(panel: &tauri::WebviewWindow) {
    let Ok(Some(monitor)) = panel.current_monitor() else {
        return;
    };
    let screen = monitor.size();
    let origin = monitor.position();
    let width = panel.outer_size().map(|s| s.width).unwrap_or(0) as f64;
    let x = origin.x as f64 + (screen.width as f64 - width) / 2.0;
    let y = origin.y as f64 + screen.height as f64 * PANEL_TOP_FRACTION;
    let _ = panel.set_position(PhysicalPosition::new(x, y));
}

/// Read the active browser tab's URL via Windows UI Automation.
/// windows-sys 0.59 lacks VARIANT, IUIAutomationElementArray, and SysFreeString
/// so direct COM UIA is not viable without pulling the full `windows` crate.
/// Uses a hidden PowerShell with CREATE_NO_WINDOW instead — ~200-400ms on
/// modern hardware, no console flash. The panel opens instantly with clipboard
/// text and updates when this returns.
#[cfg(target_os = "windows")]
fn active_browser_url() -> Option<String> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;

    let script = r#"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
try {
    $fg = [System.Windows.Automation.AutomationElement]::FocusedElement
    if (-not $fg) { exit }
    $root = $fg
    while ($root.Current.ControlType -ne [System.Windows.Automation.ControlType]::Window) {
        $parent = [TreeWalker]::ParentWalker.GetParent($root)
        if (-not $parent) { break }
        $root = $parent
    }
    $cond = New-Object System.Windows.Automation.OrCondition(
        (New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Edit)),
        (New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Document))
    )
    $elements = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
    foreach ($el in $elements) {
        $val = $el.GetCurrentPropertyValue(
            [System.Windows.Automation.ValuePattern]::ValueProperty, $true)
        if ($val -and $val -match '^https?://') { Write-Output $val; exit }
        $name = $el.Current.Name
        if ($name -and $name -match '^https?://') { Write-Output $name; exit }
    }
} catch {}
"#;
    let out = std::process::Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", script])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let url = String::from_utf8(out.stdout).ok()?.trim().to_string();
    (!url.is_empty()).then_some(url)
}

#[cfg(not(target_os = "windows"))]
fn active_browser_url() -> Option<String> {
    None
}

fn show_panel(app: &AppHandle) {
    let Some(panel) = app.get_webview_window("panel") else {
        return;
    };
    place_panel(&panel);
    let _ = panel.show();
    let _ = panel.set_focus();
    // Every show re-reads the clipboard and tries UIA for the active tab URL.
    // The panel prefers the browser URL over clipboard when both arrive.
    let _ = panel.emit(
        "panel-shown",
        json!({
            "clipboard": clipboard_text(),
            "active_url": active_browser_url(),
        }),
    );
}

fn hide_panel(app: &AppHandle) {
    if let Some(panel) = app.get_webview_window("panel") {
        let _ = panel.hide();
    }
}

fn toggle_panel(app: &AppHandle) {
    let visible = app
        .get_webview_window("panel")
        .and_then(|p| p.is_visible().ok())
        .unwrap_or(false);
    if visible {
        hide_panel(app);
    } else {
        show_panel(app);
    }
}

fn show_main(app: &AppHandle) {
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.show();
        let _ = main.set_focus();
    }
}

fn setup_tray(app: &AppHandle) -> tauri::Result<()> {
    let quick = MenuItem::with_id(app, "quick", "Quick panel", true, None::<&str>)?;
    let library = MenuItem::with_id(app, "library", "Library…", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&quick, &library, &settings, &quit])?;
    TrayIconBuilder::with_id("main")
        .icon(app.default_window_icon().expect("bundled icon").clone())
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "quick" => toggle_panel(app),
            "library" | "settings" => show_main(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;
    Ok(())
}

fn setup_hotkey(app: &AppHandle) -> tauri::Result<()> {
    // Load saved hotkey or fall back to platform default.
    let hotkey = read_saved_hotkey(app)
        .and_then(|s| parse_shortcut(&s))
        .unwrap_or_else(default_hotkey);

    app.plugin(
        tauri_plugin_global_shortcut::Builder::new()
            .with_handler(move |app, pressed, event| {
                if event.state() == ShortcutState::Pressed && pressed == &hotkey {
                    toggle_panel(app);
                }
            })
            .build(),
    )?;
    // A taken hotkey is a nuisance, not a fatal error — the user can pick
    // another in Settings. Log it so they see why nothing happens.
    if let Err(e) = app.global_shortcut().register(hotkey) {
        log::warn!(
            "global shortcut {} not registered: {e}",
            shortcut_label(&hotkey)
        );
    }
    Ok(())
}

fn main() {
    env_logger::init();
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(SidecarManager::new())
        .invoke_handler(tauri::generate_handler![
            sidecar_initialize,
            sidecar_map,
            sidecar_estimate,
            sidecar_scrape_start,
            sidecar_push,
            sidecar_cancel,
            sidecar_browser_status,
            sidecar_install_browsers,
            open_path,
            panel_hide,
            panel_resize,
            store::config_get,
            store::config_set,
            store::pick_folder,
            store::library_dir,
            store::library_list,
            store::library_delete,
            store::detect_agents,
            store::open_in_agent,
            store::detect_node,
            store::mcp_index,
            store::keychain_status,
            store::keychain_set,
            store::keychain_clear,
            schedule::schedule_get,
            schedule::schedule_set,
            autostart::autostart_get,
            autostart::autostart_set,
            hotkey_get,
            hotkey_set,
            about::about_info
        ])
        .setup(|app| {
            setup_tray(app.handle())?;
            setup_hotkey(app.handle())?;
            // Resident background loop: fires due auto re-scrapes while the app runs.
            schedule::spawn_scheduler(
                app.handle().clone(),
                app.state::<SidecarManager>().inner().clone(),
            );
            if let Some(panel) = app.get_webview_window("panel") {
                place_panel(&panel);
            }
            // First run: nothing works until a library folder exists, so the
            // chooser comes to the user instead of hiding behind the tray.
            if store::config_get(app.handle().clone())
                .map(|c| c["config"].is_null())
                .unwrap_or(false)
            {
                show_main(app.handle());
            }
            // The 1s pre-Enter budget is a measurement, not a vibe: the panel
            // reports one of these per show and it lands in the app log.
            app.listen_any("panel-timing", |event| {
                log::info!("panel-timing {}", event.payload());
            });
            // Headless verification hook: same code path as the hotkey, so the
            // show->interactive->estimate measurement works without injecting keys.
            if std::env::var_os("IOFAST_SHOW_PANEL").is_some() {
                // Delayed so the webview's listeners exist, same as any real
                // hotkey press seconds after launch.
                let handle = app.handle().clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_secs(2));
                    show_panel(&handle);
                });
            }
            Ok(())
        })
        // Resident tray app: closing hides. The panel also hides on focus loss.
        .on_window_event(|window, event| match event {
            WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                let _ = window.hide();
            }
            WindowEvent::Focused(false) if window.label() == "panel" => {
                let _ = window.hide();
            }
            _ => {}
        })
        .build(tauri::generate_context!())
        .expect("failed to build iofast")
        .run(|app, event| {
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                app.state::<SidecarManager>().shutdown();
            }
        });
}
