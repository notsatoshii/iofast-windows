#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;
mod store;

use serde_json::{json, Value};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{
    AppHandle, Emitter, Listener, LogicalSize, Manager, PhysicalPosition, State, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri_plugin_opener::OpenerExt;

use sidecar::SidecarManager;

#[cfg(target_os = "macos")]
const HOTKEY_MOD: Modifiers = Modifiers::SUPER;
#[cfg(not(target_os = "macos"))]
const HOTKEY_MOD: Modifiers = Modifiers::CONTROL;

/// Panel geometry. Width is fixed; height follows the content (`panel_resize`).
const PANEL_WIDTH: f64 = 680.0;
const PANEL_TOP_FRACTION: f64 = 0.18;

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
) -> Result<u64, String> {
    let mut params = json!({ "url": url, "out": out, "scope": scope });
    // Omitted rather than null: docs/RPC.md types these, and the engine defaults them.
    if let Some(d) = depth {
        params["depth"] = json!(d);
    }
    if let Some(m) = max_pages {
        params["max_pages"] = json!(m);
    }
    if update == Some(true) {
        params["update"] = json!(true);
    }
    // docs/RPC.md "API-key handoff": read from the keychain here, send in the
    // request body, per run. Never argv, never the sidecar's spawn environment.
    let env = store::scrape_env();
    if !env.is_empty() {
        params["env"] = Value::Object(env);
    }
    let (rid, rx) = sc.start_request(&app, "scrape", params)?;
    // Both windows track the run from this, so a scrape started in the quick
    // panel still shows its progress strip in the main window.
    let _ = app.emit("scrape-started", json!({ "rid": rid, "url": url, "out": out, "update": update == Some(true) }));
    tauri::async_runtime::spawn(async move {
        let payload = match rx.await {
            Ok(Ok(result)) => json!({ "rid": rid, "result": result }),
            Ok(Err(message)) => json!({ "rid": rid, "error": message }),
            Err(_) => json!({ "rid": rid, "error": "sidecar exited before replying" }),
        };
        let _ = tauri::Emitter::emit(&app, "scrape-done", payload);
    });
    Ok(rid)
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

/// Clipboard read, macOS only.
///
/// `tauri-plugin-clipboard-manager` would be the portable route, but its
/// `arboard` dependency turns on `image-data` by default (feature unification
/// means we cannot switch it off from here), and that pulls image -> moxcms /
/// zune-jpeg / getrandom releases requiring edition2024 into the *macOS* graph,
/// which cargo 1.78 refuses to parse. Pinning arboard and image back just moved
/// the failure one crate along. `pbpaste` is a macOS built-in, and the panel is
/// a macOS menubar utility; revisit when the toolchain floor moves.
fn clipboard_text() -> Option<String> {
    #[cfg(target_os = "macos")]
    {
        let out = std::process::Command::new("/usr/bin/pbpaste").output().ok()?;
        let text = String::from_utf8(out.stdout).ok()?;
        return (!text.trim().is_empty()).then_some(text);
    }
    #[cfg(not(target_os = "macos"))]
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

fn show_panel(app: &AppHandle) {
    let Some(panel) = app.get_webview_window("panel") else {
        return;
    };
    place_panel(&panel);
    let _ = panel.show();
    let _ = panel.set_focus();
    // Every show re-reads the clipboard: the whole pre-Enter pass hangs off this.
    let _ = panel.emit("panel-shown", json!({ "clipboard": clipboard_text() }));
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
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&quick, &library, &quit])?;
    TrayIconBuilder::with_id("main")
        .icon(app.default_window_icon().expect("bundled icon").clone())
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "quick" => toggle_panel(app),
            "library" => show_main(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;
    Ok(())
}

/// CmdOrCtrl+Shift+K.
fn quick_panel_hotkey() -> Shortcut {
    Shortcut::new(Some(HOTKEY_MOD | Modifiers::SHIFT), Code::KeyK)
}

fn setup_hotkey(app: &AppHandle) -> tauri::Result<()> {
    app.plugin(
        tauri_plugin_global_shortcut::Builder::new()
            .with_handler(|app, pressed, event| {
                if event.state() == ShortcutState::Pressed && pressed == &quick_panel_hotkey() {
                    toggle_panel(app);
                }
            })
            .build(),
    )?;
    // A taken hotkey is a nuisance, not a fatal error.
    if let Err(e) = app.global_shortcut().register(quick_panel_hotkey()) {
        log::warn!("global shortcut CmdOrCtrl+Shift+K not registered: {e}");
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
            sidecar_cancel,
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
            store::keychain_status,
            store::keychain_set,
            store::keychain_clear
        ])
        .setup(|app| {
            setup_tray(app.handle())?;
            setup_hotkey(app.handle())?;
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
