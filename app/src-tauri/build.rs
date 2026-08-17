use std::process::Command;

/// Two-sided provenance: bake in the SHA of the last commit that changed the
/// engine's bundle inputs — the SAME value engine/packaging/iofast.spec stamps
/// into the frozen sidecar — so the app can tell a stale sidecar apart from
/// itself by plain equality on an end-user machine, where no gate runs.
/// Path list mirrors iofast.spec + verify_frozen.py BUNDLE_INPUTS.
/// No git / no history = "dev": the app still builds; the check is skipped.
fn engine_tip() -> String {
    let out = Command::new("git")
        .args(["-C", env!("CARGO_MANIFEST_DIR"), "log", "-1", "--format=%H", "--"])
        .args(["../../engine/src", "../../engine/packaging", "../../engine/pyproject.toml"])
        .output();
    match out {
        Ok(o) if o.status.success() => {
            let sha = String::from_utf8_lossy(&o.stdout).trim().to_string();
            if sha.is_empty() { "dev".into() } else { sha }
        }
        _ => "dev".into(),
    }
}

fn main() {
    println!("cargo:rustc-env=IOFAST_APP_BUILD={}", engine_tip());
    // Re-stamp on every commit / checkout: logs/HEAD is per-worktree and
    // appended on each ref move.
    if let Ok(o) = Command::new("git")
        .args(["-C", env!("CARGO_MANIFEST_DIR"), "rev-parse", "--git-path", "logs/HEAD"])
        .output()
    {
        println!("cargo:rerun-if-changed={}", String::from_utf8_lossy(&o.stdout).trim());
    }
    tauri_build::build()
}
