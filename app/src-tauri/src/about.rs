//! The About panel's one command: app + engine versions and the compiled-in
//! third-party notices. The notices are `include_str!`'d at build time from the
//! worktree-root file, so there is no runtime path and no tauri.conf asset to
//! keep in sync — the binary always carries the exact list it was built with.

use serde_json::{json, Value};
use tauri::{AppHandle, State};

use crate::sidecar::SidecarManager;

/// docs/RPC.md `initialize` returns `{protocol, engine, build, manifest_schema}`;
/// the engine version and freeze-time git SHA are read from there live. A
/// sidecar that won't start (no binary, first-run) simply reports "unknown"
/// rather than failing the panel.
#[tauri::command]
pub async fn about_info(app: AppHandle, sc: State<'_, SidecarManager>) -> Result<Value, String> {
    let (engine_version, engine_build) = match sc.request(&app, "initialize", json!({})).await {
        Ok(v) => (
            v.get("engine").and_then(Value::as_str).unwrap_or("unknown").to_string(),
            v.get("build").and_then(Value::as_str).unwrap_or("unknown").to_string(),
        ),
        Err(_) => ("unknown".to_string(), "unknown".to_string()),
    };
    Ok(json!({
        "app_version": app.package_info().version.to_string(),
        "engine_version": engine_version,
        // The stamp that makes a stale bundle self-diagnosing from the About
        // panel: "unknown" = pre-stamp bundle, "dev" = unfrozen sidecar.
        "engine_build": engine_build,
        // The same stamp as baked into this app (build.rs); About shows both
        // so a mismatch banner can be read against its numbers.
        "app_build": crate::sidecar::APP_BUILD,
        // ../../../ from app/src-tauri/src/ = the worktree root, where A3 landed it.
        "notices": include_str!("../../../THIRD-PARTY-NOTICES.md"),
    }))
}
