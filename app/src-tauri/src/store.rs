//! Everything that outlives a run: the config file, the library on disk, and
//! the secret store. Plus platform shell-outs (folder picker, Trash) — see the
//! comments at each call site for why they are not crates.
//!
//! The library has no index and no database. Plain markdown on disk is the
//! product; `<library_root>/*/manifest.json` (schema 1, written by the engine)
//! is the only metadata, re-scanned on window focus.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use tauri::{AppHandle, Manager};

/// macOS: `security -s` service name. Windows: Credential Manager target prefix.
/// Accounts are the env var names below.
const KEYCHAIN_SERVICE: &str = "iofast";
/// docs/RPC.md: the `env` allowlist the sidecar accepts, verbatim.
pub const KEY_NAMES: [&str; 3] = ["OPENROUTER_API_KEY", "IOFAST_RESCUE_MODEL", "IOFAST_PROXY"];

#[derive(Serialize, Deserialize, Clone)]
pub struct Config {
    pub library_root: String,
    pub large_crawl_threshold: u32,
    // Manual runs open a headed browser for a human to clear a Cloudflare
    // challenge the stealth tier can't. `default` so pre-existing config files
    // (no key) read back as off; scheduled runs never honour it (no human).
    #[serde(default)]
    pub interactive: bool,
    /// When on, grab files the finished run into the Obsidian vault automatically.
    /// Defaults to true; a user who turns it off keeps it off (config value false wins).
    #[serde(default = "default_file_to_vault")]
    pub file_to_vault: bool,
    /// Override for the Obsidian vault path; None = auto-detect via vault/detect.py.
    #[serde(default)]
    pub vault_path: Option<String>,
    /// Whisper model id for transcript steps; None = engine default.
    #[serde(default)]
    pub whisper_model: Option<String>,
    /// Cron expression for periodic vault health checks; None = disabled.
    #[serde(default)]
    pub vault_check_schedule: Option<String>,
    /// Cron expression for weekly digest generation; None = disabled.
    #[serde(default)]
    pub digest_schedule: Option<String>,
}

fn default_file_to_vault() -> bool {
    true
}

fn config_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .app_config_dir()
        .map_err(|e| e.to_string())?
        .join("config.json"))
}

fn read_config(app: &AppHandle) -> Option<Config> {
    let text = std::fs::read_to_string(config_path(app).ok()?).ok()?;
    serde_json::from_str(&text).ok()
}

/// `null` config = first run; the main window shows the library-root chooser.
#[tauri::command]
pub fn config_get(app: AppHandle) -> Result<Value, String> {
    let suggested = app
        .path()
        .document_dir()
        .map_err(|e| e.to_string())?
        .join("iofast");
    Ok(serde_json::json!({
        "config": read_config(&app),
        "suggested_root": suggested.to_string_lossy(),
    }))
}

#[tauri::command]
pub fn config_set(app: AppHandle, config: Config) -> Result<(), String> {
    if config.library_root.trim().is_empty() {
        return Err("library root cannot be empty".into());
    }
    std::fs::create_dir_all(&config.library_root).map_err(|e| e.to_string())?;
    let path = config_path(&app)?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(&config).map_err(|e| e.to_string())?;
    std::fs::write(path, text).map_err(|e| e.to_string())
}

fn library_root(app: &AppHandle) -> Result<PathBuf, String> {
    read_config(app)
        .map(|c| PathBuf::from(c.library_root))
        .ok_or_else(|| "no library folder chosen yet".to_string())
}

/// One run directory per host+scope, hyphen-named so Spotlight and Alfred can
/// read it: docs.example.com + /docs/ -> `docs-example-com-docs`.
///
/// ponytail: two scopes differing only by a literal hyphen (`/a-b/` vs `/a/b/`)
/// collide into one directory. Add a short hash suffix if that ever bites.
pub fn run_dir_name(host: &str, scope: &str) -> Result<String, String> {
    let mut name = String::new();
    let mut dash = true; // starts true so a leading separator is dropped
    for c in host.chars().chain(scope.chars()) {
        if c.is_alphanumeric() {
            name.extend(c.to_lowercase());
            dash = false;
        } else if !dash {
            name.push('-');
            dash = true;
        }
    }
    let name = name.trim_end_matches('-').to_string();
    if name.is_empty() {
        return Err(format!("no usable directory name in {host:?} {scope:?}"));
    }
    Ok(name)
}

/// Where a scrape of `host` under `scope` lands. The engine writes wherever we
/// say, so this is the shell's one chance to keep the library tidy.
#[tauri::command]
pub fn library_dir(app: AppHandle, host: String, scope: String) -> Result<String, String> {
    let dir = library_root(&app)?.join(run_dir_name(&host, &scope)?);
    Ok(dir.to_string_lossy().into_owned())
}

/// One library row. Every field comes from the run's own manifest.json.
#[derive(Serialize)]
pub struct Run {
    dir: String,
    host: String,
    scope: String,
    url: String,
    pages: usize,
    tokens: u64,
    clean: bool,
    /// `quality.verdict`: "OK", "SUSPECT" or "unrated". Empty for runs written
    /// before the verdict existed — the row shows no badge rather than guessing.
    quality: String,
    cancelled: bool,
    modified_ms: u64,
    /// A `Stop`ped crawl leaves `.crawl/checkpoint.pkl`; its presence is what the
    /// row's Resume action keys on (the engine's `scrape resume` needs it).
    resumable: bool,
}

fn host_of(url: &str) -> String {
    let rest = url.split_once("://").map(|(_, r)| r).unwrap_or(url);
    rest.split(['/', '?', '#']).next().unwrap_or("").to_string()
}

/// Pages actually on disk, not enqueued: the row must match the folder.
///
/// A fileless ledger row carries `"file": ""`, never null, so testing
/// `!is_null()` counted every failure as a page. That was nearly invisible while
/// a failed fetch consumed a page slot; now that the crawl budget frees the slot
/// instead, a run can carry up to 3x max_pages ledger rows — and a 2-file scrape
/// of a single-page app on a static host advertised "15 pages".
fn pages_on_disk(manifest: &Value) -> usize {
    manifest
        .get("pages")
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter(|r| r.get("file").and_then(Value::as_str).is_some_and(|f| !f.is_empty()))
                .count()
        })
        .unwrap_or(0)
}

fn read_run(dir: &Path) -> Option<Run> {
    let manifest = dir.join("manifest.json");
    let value: Value = serde_json::from_str(&std::fs::read_to_string(&manifest).ok()?).ok()?;
    let info = value.get("info")?;
    let url = info.get("start url")?.as_str().unwrap_or_default().to_string();
    let modified_ms = manifest
        .metadata()
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    Some(Run {
        dir: dir.to_string_lossy().into_owned(),
        host: host_of(&url),
        scope: info.get("scope").and_then(Value::as_str).unwrap_or("/").to_string(),
        url,
        pages: pages_on_disk(&value),
        tokens: value.get("token_estimate").and_then(Value::as_u64).unwrap_or(0),
        clean: value
            .get("clean")
            .and_then(|c| c.get("clean"))
            .and_then(Value::as_bool)
            .unwrap_or(false),
        quality: value
            .get("quality")
            .and_then(|q| q.get("verdict"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        cancelled: info.get("cancelled").and_then(Value::as_bool).unwrap_or(false),
        modified_ms,
        resumable: dir.join(".crawl/checkpoint.pkl").is_file(),
    })
}

/// Scans `<library_root>/*/manifest.json`. Cheap for a personal library; the
/// window re-runs it on focus. TODO: cache an index once libraries get big.
#[tauri::command]
pub fn library_list(app: AppHandle) -> Result<Vec<Run>, String> {
    let root = library_root(&app)?;
    let entries = match std::fs::read_dir(&root) {
        Ok(entries) => entries,
        // A library root the user deleted or unplugged is an empty list, not an error.
        Err(_) => return Ok(Vec::new()),
    };
    let mut runs: Vec<Run> = entries
        .flatten()
        .filter(|e| e.path().is_dir())
        .filter_map(|e| read_run(&e.path()))
        .collect();
    runs.sort_by(|a, b| b.modified_ms.cmp(&a.modified_ms));
    Ok(runs)
}

/// Move a run directory to the Trash — never `rm -rf`, and never anything the
/// user did not put in their library: `path` arrives from the webview, so it is
/// checked to be a direct child of the configured root before anything moves.
#[tauri::command]
pub fn library_delete(app: AppHandle, path: String) -> Result<(), String> {
    let root = library_root(&app)?.canonicalize().map_err(|e| e.to_string())?;
    let dir = PathBuf::from(&path).canonicalize().map_err(|e| e.to_string())?;
    if dir.parent() != Some(root.as_path()) || !dir.is_dir() {
        return Err(format!("{path} is not a run folder in the library"));
    }
    trash::delete(&dir).map_err(|e| e.to_string())
}

/// Folder picker. macOS uses `choose folder` via osascript (no TCC prompt, no
/// crate). Windows uses PowerShell's `FolderBrowserDialog` via a short inline
/// script — same reason: `tauri-plugin-dialog` pulls edition2024 deps that
/// cargo 1.78 could not parse, and the toolchain floor moved but the shell-out
/// stayed because it works without new dependencies on either platform.
#[tauri::command]
pub fn pick_folder(start: Option<String>) -> Result<Option<String>, String> {
    #[cfg(target_os = "macos")]
    {
        fn applescript_string(text: &str) -> String {
            format!("\"{}\"", text.replace('\\', "\\\\").replace('"', "\\\""))
        }
        let prompt = "Choose the folder where iofast keeps your library";
        let mut script = format!(
            "POSIX path of (choose folder with prompt {}",
            applescript_string(prompt)
        );
        match start {
            Some(dir) if Path::new(&dir).is_dir() => {
                script.push_str(&format!(
                    " default location POSIX file {}",
                    applescript_string(&dir)
                ));
            }
            _ => {}
        }
        script.push(')');
        let out = std::process::Command::new("/usr/bin/osascript")
            .args(["-e", &script])
            .output()
            .map_err(|e| e.to_string())?;
        if !out.status.success() {
            let err = String::from_utf8_lossy(&out.stderr);
            return if err.contains("-128") || err.contains("canceled") {
                Ok(None)
            } else {
                Err(err.trim().to_string())
            };
        }
        let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
        Ok(Some(path.trim_end_matches('/').to_string()))
    }
    #[cfg(target_os = "windows")]
    {
        // FolderBrowserDialog in a single-threaded STA apartment. The initial
        // directory is set only when the caller passed one that exists; an
        // invalid path would throw, so we silently ignore it.
        let init = match start {
            Some(dir) if Path::new(&dir).is_dir() => dir,
            _ => String::new(),
        };
        // Escape single quotes for the PowerShell string literal.
        let init_ps = init.replace('\'', "''");
        let script = format!(
            "Add-Type -AssemblyName System.Windows.Forms; \
             $d = New-Object System.Windows.Forms.FolderBrowserDialog; \
             $d.Description = 'Choose the folder where iofast keeps your library'; \
             $d.ShowNewFolderButton = $true; \
             if ('{init_ps}' -ne '' ) {{ $d.SelectedPath = '{init_ps}' }}; \
             if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{ \
               Write-Output $d.SelectedPath \
             }}"
        );
        let out = {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            std::process::Command::new("powershell")
                .args(["-NoProfile", "-NonInteractive", "-Command", &script])
                .creation_flags(CREATE_NO_WINDOW)
                .output()
                .map_err(|e| e.to_string())?
        };
        if !out.status.success() {
            let err = String::from_utf8_lossy(&out.stderr);
            return Err(err.trim().to_string());
        }
        let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if path.is_empty() {
            return Ok(None); // user cancelled
        }
        Ok(Some(path))
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = start;
        Err("folder picker not supported on this platform".into())
    }
}

/// Read one secret from the platform store. macOS uses `security find-generic-password`;
/// Windows uses Win32 CredReadW (no PowerShell, no argv leakage).
/// Returns None when the entry does not exist or the read fails.
fn keychain_get(name: &str) -> Option<String> {
    #[cfg(target_os = "macos")]
    {
        let out = std::process::Command::new("/usr/bin/security")
            .args(["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"])
            .output()
            .ok()?;
        if !out.status.success() {
            return None;
        }
        let value = String::from_utf8(out.stdout).ok()?.trim_end().to_string();
        (!value.is_empty()).then_some(value)
    }
    #[cfg(target_os = "windows")]
    {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;
        unsafe {
            use windows_sys::Win32::Security::Credentials::{CredReadW, CREDENTIALW, CRED_TYPE_GENERIC};
            let target: Vec<u16> = OsStr::new(&format!("{KEYCHAIN_SERVICE}:{name}"))
                .encode_wide()
                .chain(std::iter::once(0))
                .collect();
            let mut cred: *mut CREDENTIALW = std::ptr::null_mut();
            if CredReadW(target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut cred) == 0 {
                return None;
            }
            if cred.is_null() || (*cred).CredentialBlobSize == 0 || (*cred).CredentialBlob.is_null() {
                windows_sys::Win32::Security::Credentials::CredFree(cred as *mut _);
                return None;
            }
            let blob = std::slice::from_raw_parts(
                (*cred).CredentialBlob,
                (*cred).CredentialBlobSize as usize,
            );
            // Credentials are stored as UTF-8 bytes (written by keychain_set).
            let value = String::from_utf8_lossy(blob).trim().to_string();
            windows_sys::Win32::Security::Credentials::CredFree(cred as *mut _);
            (!value.is_empty()).then_some(value)
        }
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = name;
        None
    }
}

/// Which secrets exist — never their values. The settings pane shows a saved
/// state and a Clear button; nothing reads a stored key back into the webview.
#[tauri::command]
pub fn keychain_status() -> Map<String, Value> {
    KEY_NAMES
        .iter()
        .map(|name| ((*name).to_string(), Value::Bool(keychain_get(name).is_some())))
        .collect()
}

/// Store one secret. macOS uses `security add-generic-password` with the value
/// on stdin (argv is world-readable in `ps`). Windows uses Win32 CredWriteW
/// directly — no command line, no PowerShell, no argv leakage.
#[tauri::command]
pub fn keychain_set(name: String, value: String) -> Result<(), String> {
    if !KEY_NAMES.contains(&name.as_str()) {
        return Err(format!("unknown key {name}"));
    }
    if value.is_empty() {
        return Err("value cannot be empty".into());
    }
    if value.contains('\n') {
        return Err("value cannot contain a line break".into());
    }
    #[cfg(target_os = "macos")]
    {
        use std::io::Write;
        let mut child = std::process::Command::new("/usr/bin/security")
            .args(["add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", &name, "-w"])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .map_err(|e| e.to_string())?;
        let written = child
            .stdin
            .take()
            .expect("piped")
            .write_all(format!("{value}\n{value}\n").as_bytes());
        let status = child.wait().map_err(|e| e.to_string())?;
        written.map_err(|e| e.to_string())?;
        if !status.success() {
            return Err("keychain refused the item".into());
        }
        if keychain_get(&name).as_deref() != Some(value.as_str()) {
            return Err("keychain did not store the value".into());
        }
        Ok(())
    }
    #[cfg(target_os = "windows")]
    {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;
        unsafe {
            use windows_sys::Win32::Security::Credentials::{
                CredWriteW, CREDENTIALW, CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC,
            };
            let target: Vec<u16> = OsStr::new(&format!("{KEYCHAIN_SERVICE}:{name}"))
                .encode_wide()
                .chain(std::iter::once(0))
                .collect();
            let blob = value.as_bytes();
            let mut target_mut = target.clone();
            let cred = CREDENTIALW {
                Flags: 0,
                Type: CRED_TYPE_GENERIC,
                TargetName: target_mut.as_mut_ptr(),
                Comment: std::ptr::null_mut(),
                LastWritten: windows_sys::Win32::Foundation::FILETIME {
                    dwLowDateTime: 0,
                    dwHighDateTime: 0,
                },
                CredentialBlobSize: blob.len() as u32,
                CredentialBlob: blob.as_ptr() as *mut u8,
                Persist: CRED_PERSIST_LOCAL_MACHINE,
                AttributeCount: 0,
                Attributes: std::ptr::null_mut(),
                TargetAlias: std::ptr::null_mut(),
                UserName: std::ptr::null_mut(),
            };
            if CredWriteW(&cred, 0) == 0 {
                return Err("Credential Manager refused the item".into());
            }
        }
        if keychain_get(&name).as_deref() != Some(value.as_str()) {
            return Err("Credential Manager did not store the value".into());
        }
        Ok(())
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (name, value);
        Err("secret store not supported on this platform".into())
    }
}

#[tauri::command]
pub fn keychain_clear(name: String) -> Result<(), String> {
    if !KEY_NAMES.contains(&name.as_str()) {
        return Err(format!("unknown key {name}"));
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("/usr/bin/security")
            .args(["delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", &name])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "windows")]
    {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;
        unsafe {
            use windows_sys::Win32::Security::Credentials::{CredDeleteW, CRED_TYPE_GENERIC};
            let target: Vec<u16> = OsStr::new(&format!("{KEYCHAIN_SERVICE}:{name}"))
                .encode_wide()
                .chain(std::iter::once(0))
                .collect();
            // CredDeleteW returns 0 on failure; ERROR_NOT_FOUND (1168) is fine.
            let rc = CredDeleteW(target.as_ptr(), CRED_TYPE_GENERIC, 0);
            if rc == 0 {
                let err = std::io::Error::last_os_error();
                if err.raw_os_error() != Some(1168) {
                    return Err(format!("could not delete credential: {err}"));
                }
            }
        }
    }
    Ok(())
}

/// docs/RPC.md `scrape.env`: request-scoped secrets, read fresh from the
/// keychain per run and sent in the request body. Absent keys are simply
/// omitted — an empty value would arm the rescue with a broken key.
pub fn scrape_env() -> Map<String, Value> {
    KEY_NAMES
        .iter()
        .filter_map(|name| keychain_get(name).map(|v| ((*name).to_string(), Value::String(v))))
        .collect()
}

// ---- ingest mode 1: open a run folder in a coding-agent CLI (PLAN Phase 3) ----

/// A coding agent we know how to launch. The webview sends only an `id` from
/// this table; the binary is resolved to an absolute path here, so the webview
/// can never ask us to spawn an arbitrary program. `terminal` agents are TUIs
/// opened in a Terminal window `cd`'d to the folder; the rest are GUI editors
/// opened with the folder as an argument. No auto-approve / permission-skip flag
/// is ever added — scraped docs are untrusted input.
struct Agent {
    id: &'static str,
    bin: &'static str,
    terminal: bool,
}

const AGENTS: [Agent; 4] = [
    Agent { id: "claude", bin: "claude", terminal: true },
    Agent { id: "codex", bin: "codex", terminal: true },
    Agent { id: "gemini", bin: "gemini", terminal: true },
    Agent { id: "cursor", bin: "cursor", terminal: false },
];

fn agent_by_id(id: &str) -> Option<&'static Agent> {
    AGENTS.iter().find(|a| a.id == id)
}

/// Directories a CLI from Homebrew, npm/pnpm/bun, or a native installer lands in.
/// A GUI app launched from Finder inherits a bare PATH, so the inherited PATH
/// alone misses most of these — search a curated set alongside it.
///
/// ponytail: a CLI installed only on a PATH entry set in an interactive-shell rc
/// we never source won't be found; the user can symlink it into ~/.local/bin.
/// Widen this list if that bites.
fn candidate_dirs() -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = Vec::new();
    if let Some(path) = std::env::var_os("PATH") {
        dirs.extend(std::env::split_paths(&path));
    }
    #[cfg(target_os = "macos")]
    {
        for fixed in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] {
            dirs.push(PathBuf::from(fixed));
        }
        if let Some(home) = std::env::var_os("HOME") {
            let home = PathBuf::from(home);
            for rel in [".local/bin", ".bun/bin", ".deno/bin", ".claude/local", ".npm-global/bin"] {
                dirs.push(home.join(rel));
            }
        }
        dirs.push(PathBuf::from("/Applications/Cursor.app/Contents/Resources/app/bin"));
    }
    #[cfg(target_os = "windows")]
    {
        // Common Windows install locations for CLIs (npm, bun, deno, scoop, choco).
        if let Some(local) = std::env::var_os("LOCALAPPDATA") {
            let local = PathBuf::from(local);
            for rel in ["Programs\\Python\\Python313\\Scripts", "pnpm", "bun", "deno", "Microsoft\\WindowsApps"] {
                dirs.push(local.join(rel));
            }
        }
        if let Some(appdata) = std::env::var_os("APPDATA") {
            let appdata = PathBuf::from(appdata);
            for rel in ["npm", "scoop\\shims"] {
                dirs.push(appdata.join(rel));
            }
        }
        if let Some(progfiles) = std::env::var_os("ProgramFiles") {
            dirs.push(PathBuf::from(progfiles).join("nodejs"));
        }
        // Cursor installs its CLI shim here on Windows.
        if let Some(local) = std::env::var_os("LOCALAPPDATA") {
            dirs.push(PathBuf::from(local).join("Programs\\cursor\\resources\\app\\bin"));
        }
    }
    dirs
}

/// First directory in the search path holding an executable named `bin`.
fn resolve_bin(bin: &str) -> Option<PathBuf> {
    let mut seen = std::collections::HashSet::new();
    for dir in candidate_dirs() {
        if !seen.insert(dir.clone()) {
            continue;
        }
        // On Windows, executability is determined by extension (.exe, .cmd, .bat, .com).
        // Try each common extension when the bare name doesn't match directly.
        #[cfg(target_os = "windows")]
        {
            for ext in ["", ".exe", ".cmd", ".bat", ".com"] {
                let path = if ext.is_empty() {
                    dir.join(bin)
                } else {
                    dir.join(format!("{bin}{ext}"))
                };
                if let Ok(meta) = std::fs::metadata(&path) {
                    if meta.is_file() {
                        return Some(path);
                    }
                }
            }
        }
        #[cfg(not(target_os = "windows"))]
        {
            use std::os::unix::fs::PermissionsExt;
            let path = dir.join(bin);
            if let Ok(meta) = std::fs::metadata(&path) {
                if meta.is_file() && meta.permissions().mode() & 0o111 != 0 {
                    return Some(path);
                }
            }
        }
    }
    None
}

#[derive(Serialize)]
pub struct DetectedAgent {
    id: &'static str,
    path: String,
    terminal: bool,
}

/// Which coding-agent CLIs are installed, for the library's "Open in…" action.
#[tauri::command]
pub fn detect_agents() -> Vec<DetectedAgent> {
    AGENTS
        .iter()
        .filter_map(|a| {
            resolve_bin(a.bin).map(|p| DetectedAgent {
                id: a.id,
                path: p.to_string_lossy().into_owned(),
                terminal: a.terminal,
            })
        })
        .collect()
}

/// A library run directory, validated exactly like `library_delete`: `path`
/// arrives from the webview, so it must be a direct child of the configured root.
pub(crate) fn checked_run_dir(app: &AppHandle, path: &str) -> Result<PathBuf, String> {
    let root = library_root(app)?.canonicalize().map_err(|e| e.to_string())?;
    let dir = PathBuf::from(path).canonicalize().map_err(|e| e.to_string())?;
    if dir.parent() != Some(root.as_path()) || !dir.is_dir() {
        return Err(format!("{path} is not a run folder in the library"));
    }
    Ok(dir)
}

/// Open a scraped run folder in a coding agent. The webview passes an agent `id`
/// (resolved to a binary here — never a path from the webview) and a run dir
/// (checked to sit inside the library). The untrusted folder path is never
/// spliced into a shell string: a GUI agent gets it as an argv element, and a
/// terminal agent gets it through `osascript` run-arguments where `quoted form
/// of` shell-quotes it. Only the fixed `cd … && exec …` template is literal.
#[tauri::command]
pub fn open_in_agent(app: AppHandle, agent: String, dir: String) -> Result<(), String> {
    let spec = agent_by_id(&agent).ok_or_else(|| format!("unknown agent {agent}"))?;
    let bin = resolve_bin(spec.bin).ok_or_else(|| format!("{} is not installed", spec.id))?;
    let dir = checked_run_dir(&app, &dir)?;
    let dir = dir.to_string_lossy();
    if spec.terminal {
        #[cfg(target_os = "macos")]
        {
            // ponytail: Terminal.app only. Add iTerm detection if that's ever wanted.
            let script = "on run argv\n\
                 set d to quoted form of (item 1 of argv)\n\
                 set a to quoted form of (item 2 of argv)\n\
                 tell application \"Terminal\"\n\
                 do script \"cd \" & d & \" && exec \" & a\n\
                 activate\n\
                 end tell\n\
                 end run";
            let out = std::process::Command::new("/usr/bin/osascript")
                .args(["-e", script])
                .arg(dir.as_ref())
                .arg(&*bin)
                .output()
                .map_err(|e| e.to_string())?;
            if !out.status.success() {
                return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
            }
        }
        #[cfg(target_os = "windows")]
        {
            // Windows Terminal (wt.exe) is the modern default; fall back to cmd
            // when it is not installed. Both open a new tab/window cd'd to the
            // run folder with the agent binary ready to type at.
            let bin_str = bin.to_string_lossy();
            let wt = resolve_bin("wt");
            if wt.is_some() {
                std::process::Command::new(wt.unwrap())
                    .args(["new-tab", "--title", "iofast", "-d", dir.as_ref(), &*bin_str])
                    .spawn()
                    .map(|_| ())
                    .map_err(|e| e.to_string())?;
            } else {
                // /k keeps the window open after the agent exits so the user can
                // read any output; /d sets the working directory.
                std::process::Command::new("cmd")
                    .args(["/c", "start", "cmd", "/k", "cd", "/d", dir.as_ref(), "&&", &*bin_str])
                    .spawn()
                    .map(|_| ())
                    .map_err(|e| e.to_string())?;
            }
        }
        Ok(())
    } else {
        std::process::Command::new(&bin)
            .arg(dir.as_ref())
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
}

// ---- ingest mode 2: index a run folder for MCP search (docs-mcp-server) ----

/// Pinned to the version validated in the Phase 3 Mode 2 spike: `file://` ingest
/// works, search degrades cleanly to FTS-only with no API key, and the per-file
/// UNTRUSTED banner survives ingestion into every returned chunk (docs-mcp-server
/// can't carry that warning in its own tool descriptions, so the wrapper must).
/// It is a Node package run through `npx`; without Node the FastMCP fallback
/// (PLAN Phase 3 mode 2) applies — not built.
const DOCS_MCP_PKG: &str = "@arabold/docs-mcp-server@3.0.0";

/// Whether the MCP runtime is present, for the library's "MCP search" action.
/// Only `npx` is needed — it fetches and runs docs-mcp-server on demand.
#[tauri::command]
pub fn detect_node() -> Option<String> {
    resolve_bin("npx").map(|p| p.to_string_lossy().into_owned())
}

/// One shared docs-mcp index for the whole library: each indexed run becomes a
/// named library inside it, so the user pastes a single config that searches all
/// of them. The engine's markdown on disk stays the source of truth; this SQLite
/// is a derived, rebuildable index.
fn docs_mcp_store(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_data_dir().map_err(|e| e.to_string())?.join("docs-mcp-store");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir)
}

/// docs-mcp-server v3 gates `file://` ingest behind an allowed-roots policy
/// (default `$DOCUMENTS`). The library can live wherever the user chose, so pin
/// the real library root as an allowed root — the spike proved the default alone
/// rejects a library outside ~/Documents.
fn write_docs_mcp_config(app: &AppHandle, root: &Path) -> Result<PathBuf, String> {
    let cfg = serde_json::json!({
        "scraper": { "security": { "fileAccess": {
            "mode": "allowedRoots",
            "allowedRoots": [root.to_string_lossy()],
        }}}
    });
    let path = config_path(app)?.with_file_name("docs-mcp-config.json");
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(&cfg).map_err(|e| e.to_string())?;
    std::fs::write(&path, text).map_err(|e| e.to_string())?;
    Ok(path)
}

/// The MCP client config the user pastes into Claude Desktop / Cursor. The server
/// it launches runs `--read-only`: search only, never a scrape triggered by the
/// agent over untrusted docs, against the shared store this app indexes into.
fn stdio_config_json(npx: &Path, store: &Path) -> String {
    let cfg = serde_json::json!({
        "mcpServers": {
            "iofast-docs": {
                "command": npx.to_string_lossy(),
                "args": [
                    "-y", DOCS_MCP_PKG,
                    "--protocol", "stdio",
                    "--read-only",
                    "--telemetry", "false",
                    "--store-path", store.to_string_lossy(),
                    "mcp",
                ],
            }
        }
    });
    serde_json::to_string_pretty(&cfg).expect("json")
}

/// npx runs node through its `#!/usr/bin/env node` shebang. A Finder-launched app
/// inherits a bare PATH that usually lacks node's dir (nvm/bun/volta), so prepend
/// npx's own directory — node lives beside it — to the child PATH.
///
/// ponytail: an nvm `npx` is version-pinned in its path, so the pasted config
/// breaks if the user later removes that node version. Re-index to refresh it.
fn npx_command(npx: &Path) -> std::process::Command {
    let mut cmd = std::process::Command::new(npx);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    if let Some(parent) = npx.parent() {
        let path = std::env::var("PATH").unwrap_or_default();
        cmd.env("PATH", format!("{}:{}", parent.display(), path));
    }
    cmd
}

#[derive(Serialize)]
pub struct McpIndexed {
    library: String,
    config_json: String,
}

/// Index one run folder into the shared docs-mcp store, then hand back the paste
/// config. `dir` arrives from the webview, so it is checked to be a library child
/// (like `open_in_agent`); the folder name is the docs-mcp library id and reaches
/// npx as an argv element, and the run path as a `file://` argv element — never a
/// shell string. Blocks until indexing finishes — seconds for a personal library.
///
/// ponytail: a huge run (thousands of pages) blocks this command for its whole
/// index. Tauri runs it off the UI thread so the window stays live, but there is
/// no progress bar; wire the engine's event stream in if that ever bites.
#[tauri::command]
pub fn mcp_index(app: AppHandle, dir: String) -> Result<McpIndexed, String> {
    let npx = resolve_bin("npx").ok_or_else(|| "Node.js (npx) is not installed".to_string())?;
    let run_dir = checked_run_dir(&app, &dir)?;
    let library = run_dir
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| "run folder has no name".to_string())?
        .to_string();
    let store = docs_mcp_store(&app)?;
    let cfg = write_docs_mcp_config(&app, &library_root(&app)?)?;
    let out = npx_command(&npx)
        .args(["-y", DOCS_MCP_PKG, "--quiet", "--telemetry", "false", "--config"])
        .arg(&cfg)
        .arg("--store-path")
        .arg(&store)
        .args(["scrape", &library])
        .arg(format!("file://{}", run_dir.to_string_lossy()))
        .output()
        .map_err(|e| e.to_string())?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(err.trim().lines().last().unwrap_or("indexing failed").to_string());
    }
    Ok(McpIndexed { library, config_json: stdio_config_json(&npx, &store) })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stdio_config_is_valid_json_and_read_only() {
        let json = stdio_config_json(Path::new("/usr/local/bin/npx"), Path::new("/data/store"));
        let v: Value = serde_json::from_str(&json).expect("valid json");
        let srv = &v["mcpServers"]["iofast-docs"];
        assert_eq!(srv["command"], "/usr/local/bin/npx");
        let args: Vec<&str> = srv["args"].as_array().unwrap().iter().map(|a| a.as_str().unwrap()).collect();
        // Search only over untrusted docs — never a write/scrape tool for the agent.
        assert!(args.contains(&"--read-only"), "MCP server must be read-only");
        // The exact version the spike validated, and the store the app indexed into.
        assert!(args.contains(&DOCS_MCP_PKG));
        assert!(args.contains(&"/data/store"), "store path must reach npx");
    }

    #[test]
    fn the_page_count_ignores_rows_that_produced_no_file() {
        // The must-fail input first: a failed fetch carries "file": "", not null.
        let manifest: Value = serde_json::from_str(
            r#"{"pages": [
                 {"url": "a", "file": "a.md"},
                 {"url": "b", "file": ""},
                 {"url": "c", "file": null},
                 {"url": "d"}
               ]}"#,
        )
        .unwrap();
        assert_eq!(pages_on_disk(&manifest), 1);
        assert_eq!(pages_on_disk(&serde_json::json!({})), 0);
    }

    #[test]
    fn agent_ids_are_unique_and_looked_up_by_id() {
        let ids: Vec<_> = AGENTS.iter().map(|a| a.id).collect();
        let uniq: std::collections::HashSet<_> = ids.iter().collect();
        assert_eq!(ids.len(), uniq.len(), "duplicate agent id");
        assert!(agent_by_id("claude").is_some_and(|a| a.terminal));
        assert!(agent_by_id("cursor").is_some_and(|a| !a.terminal));
        assert!(agent_by_id("nope").is_none());
    }

    #[test]
    fn resolve_bin_finds_a_known_tool_and_rejects_nonsense() {
        // `sh` is in /bin on every macOS; a random name never resolves.
        assert!(resolve_bin("sh").is_some());
        assert!(resolve_bin("iofast-not-a-real-binary-xyz").is_none());
    }

    #[test]
    fn run_dir_name_flattens_host_and_scope() {
        // The name in docs/RPC.md's example library.
        assert_eq!(run_dir_name("docs.example.com", "/docs/").unwrap(), "docs-example-com-docs");
        // Root scope adds nothing — the host alone is the whole name.
        assert_eq!(run_dir_name("docs.example.com", "/").unwrap(), "docs-example-com");
        // Nested scope, every segment kept in order.
        assert_eq!(
            run_dir_name("docs.example.com", "/docs/api/v2/").unwrap(),
            "docs-example-com-docs-api-v2"
        );
        // A port is part of the host and must survive as a segment.
        assert_eq!(run_dir_name("example.com:8080", "/docs/").unwrap(), "example-com-8080-docs");
        // No trailing slash on the scope: same name, no dangling dash.
        assert_eq!(run_dir_name("example.com", "/docs").unwrap(), "example-com-docs");
        // Case folds; a mixed-case host must not make a second directory.
        assert_eq!(run_dir_name("EXAMPLE.com", "/Docs/").unwrap(), "example-com-docs");
        // Runs of separators collapse instead of emitting empty segments.
        assert_eq!(run_dir_name("example.com", "//a__b//").unwrap(), "example-com-a-b");
        // Nothing usable is an error, never a dotfile or an empty path segment.
        assert!(run_dir_name("", "/").is_err());
        assert!(run_dir_name("...", "/").is_err());
    }

    #[test]
    fn host_of_takes_the_authority_only() {
        assert_eq!(host_of("https://docs.example.com/docs/intro"), "docs.example.com");
        assert_eq!(host_of("http://example.com:8080"), "example.com:8080");
        assert_eq!(host_of(""), "");
    }

    /// Round-trip test for the native Win32 credential store. Calls
    /// CredWriteW/CredReadW/CredDeleteW directly with a throwaway target
    /// name, bypassing the KEY_NAMES allowlist so we never touch real keys.
    #[cfg(target_os = "windows")]
    #[test]
    fn credential_round_trip_write_read_delete() {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Security::Credentials::{
            CredDeleteW, CredFree, CredReadW, CredWriteW, CREDENTIALW,
            CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC,
        };

        let target_name = "iofast:__test_roundtrip_do_not_use";
        let secret = b"test-secret-value-12345";

        let target_wide: Vec<u16> = OsStr::new(target_name)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();

        // Clean up any leftover from a previous failed run.
        unsafe {
            CredDeleteW(target_wide.as_ptr(), CRED_TYPE_GENERIC, 0);
        }

        // Write
        let mut cred = CREDENTIALW {
            Flags: 0,
            Type: CRED_TYPE_GENERIC,
            TargetName: target_wide.as_ptr() as *mut u16,
            Comment: std::ptr::null_mut(),
            LastWritten: windows_sys::Win32::Foundation::FILETIME {
                dwLowDateTime: 0,
                dwHighDateTime: 0,
            },
            CredentialBlobSize: secret.len() as u32,
            CredentialBlob: secret.as_ptr() as *mut u8,
            Persist: CRED_PERSIST_LOCAL_MACHINE,
            AttributeCount: 0,
            Attributes: std::ptr::null_mut(),
            TargetAlias: std::ptr::null_mut(),
            UserName: std::ptr::null_mut(),
        };
        let write_ok = unsafe { CredWriteW(&cred, 0) };
        assert_ne!(write_ok, 0, "CredWriteW should succeed");

        // Read back
        let mut read_cred: *mut CREDENTIALW = std::ptr::null_mut();
        let read_ok = unsafe {
            CredReadW(target_wide.as_ptr(), CRED_TYPE_GENERIC, 0, &mut read_cred)
        };
        assert_ne!(read_ok, 0, "CredReadW should succeed");
        assert!(!read_cred.is_null());
        let blob = unsafe {
            std::slice::from_raw_parts(
                (*read_cred).CredentialBlob,
                (*read_cred).CredentialBlobSize as usize,
            )
        };
        assert_eq!(blob, secret, "read-back must match what was written");
        unsafe { CredFree(read_cred as *mut _) };

        // Delete
        let del_ok =
            unsafe { CredDeleteW(target_wide.as_ptr(), CRED_TYPE_GENERIC, 0) };
        assert_ne!(del_ok, 0, "CredDeleteW should succeed");

        // Verify gone
        let mut gone_cred: *mut CREDENTIALW = std::ptr::null_mut();
        let gone_ok = unsafe {
            CredReadW(target_wide.as_ptr(), CRED_TYPE_GENERIC, 0, &mut gone_cred)
        };
        assert_eq!(gone_ok, 0, "credential must be gone after delete");

        // Double-delete should fail gracefully (ERROR_NOT_FOUND = 1168)
        let double_del =
            unsafe { CredDeleteW(target_wide.as_ptr(), CRED_TYPE_GENERIC, 0) };
        assert_eq!(double_del, 0, "double-delete should fail");
    }
}
