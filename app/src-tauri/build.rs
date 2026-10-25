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

/// The repo-root VERSION file is the single source of the version number.
/// Cargo cannot read it, so Cargo.toml carries a copy; fail the build when the
/// two differ instead of shipping a mislabelled app. tauri.conf.json has no
/// version field on purpose: Tauri then uses Cargo.toml's.
fn check_version() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../VERSION");
    println!("cargo:rerun-if-changed={path}");
    let Ok(text) = std::fs::read_to_string(path) else {
        return; // no repo around (vendored crate): nothing to compare against
    };
    let want = text.trim();
    let have = env!("CARGO_PKG_VERSION");
    if want != have {
        panic!("VERSION says {want} but app/src-tauri/Cargo.toml says {have}; bump both (docs/RELEASING.md)");
    }
}

fn main() {
    check_version();
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
