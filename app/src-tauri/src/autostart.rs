//! Start-at-login (decision D3). A LaunchAgent plist asks launchd to open THE
//! APP at login, so the resident scheduler in `schedule.rs` is actually running
//! when a run's daily/weekly re-scrape falls due. Scope is autostart only: no
//! headless twin, no second scraping process, so nothing new has to be locked.
//!
//! The plist runs `/usr/bin/open -g -a <bundle>`, not the binary inside it.
//! LaunchServices already reuses a running instance, so a stray invocation
//! activates the app instead of starting a second one — measured on this
//! machine (`open -g -a` against a running app kept the same pid, 839), which
//! is why `tauri-plugin-single-instance` is not pulled in for this.
//!
//! ponytail: no `KeepAlive` (it would fight Quit) and no `StartInterval` (the
//! app stays resident once launched, and schedule.rs already re-checks after a
//! sleep). Add an interval only if "closed all day, still expected a scrape"
//! turns out to be a real complaint.

use std::path::{Path, PathBuf};

/// Matches the bundle identifier in tauri.conf.json.
const LABEL: &str = "com.iofast.desktop";

fn plist_path() -> Result<PathBuf, String> {
    let home = std::env::var_os("HOME").ok_or("no HOME")?;
    Ok(PathBuf::from(home)
        .join("Library/LaunchAgents")
        .join(format!("{LABEL}.plist")))
}

/// `…/iofast.app/Contents/MacOS/iofast` -> `…/iofast.app`.
///
/// Refused, because each of these would write a plist that silently launches
/// nothing at some later login:
/// * a `cargo run` / `tauri dev` binary — no bundle around it at all;
/// * a copy still running from the mounted `.dmg` (`/Volumes/…`) — the volume
///   ejects and the path dies;
/// * a Gatekeeper-translocated copy (`…/AppTranslocation/<uuid>/d/…`) — the
///   build is unsigned (docs/DISTRIBUTION.md), so a quarantined first run
///   executes from a random read-only mount that is gone next boot.
///
/// All three mean the same thing to the user: drag it to Applications first.
fn bundle_of(exe: &Path) -> Result<PathBuf, String> {
    let macos = exe.parent();
    let bundle = macos
        .and_then(Path::parent) // Contents
        .and_then(Path::parent) // *.app
        .filter(|p| p.extension().is_some_and(|e| e == "app"))
        .filter(|_| macos.is_some_and(|p| p.ends_with("Contents/MacOS")))
        .ok_or("Start at login needs the installed iofast.app, not a dev build.")?;
    let text = bundle.to_string_lossy();
    if text.starts_with("/Volumes/") || text.contains("/AppTranslocation/") {
        return Err("Move iofast to your Applications folder first — \
                    started from a disk image, it would not be there at your next login."
            .into());
    }
    Ok(bundle.to_path_buf())
}

fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

fn plist_xml(app: &Path) -> String {
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>{LABEL}</string>
	<key>ProgramArguments</key>
	<array>
		<string>/usr/bin/open</string>
		<string>-g</string>
		<string>-a</string>
		<string>{app}</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
</dict>
</plist>
"#,
        app = xml_escape(&app.to_string_lossy()),
    )
}

/// On, only if the plist on disk points at THIS bundle. A plist left behind by
/// an app that has since been moved would launch nothing, so it reads as off
/// and the next enable rewrites it.
///
/// Known ceiling: macOS 13+ also lets the user switch this agent off under
/// System Settings -> General -> Login Items ("Allow in the Background")
/// WITHOUT touching the plist, and that switch is invisible from here — the
/// toggle can read On for a job launchd will not run. Reading it means adopting
/// `SMAppService`; not worth an Objective-C bridge for this slice.
fn enabled_in(text: &str, app: &Path) -> bool {
    text.contains(&format!("<string>{}</string>", xml_escape(&app.to_string_lossy())))
}

#[tauri::command]
pub fn autostart_get() -> Result<bool, String> {
    // A dev build can never be on: `autostart_set` refuses to write one.
    let Ok(bundle) = std::env::current_exe().map_err(|e| e.to_string()).and_then(|e| bundle_of(&e))
    else {
        return Ok(false);
    };
    let text = std::fs::read_to_string(plist_path()?).unwrap_or_default();
    Ok(enabled_in(&text, &bundle))
}

#[tauri::command]
pub fn autostart_set(enabled: bool) -> Result<bool, String> {
    let path = plist_path()?;
    if !enabled {
        // Deleting the plist is the whole disable: RunAtLoad only fires at the
        // next login, and the file is gone by then.
        match std::fs::remove_file(&path) {
            Ok(()) => return Ok(false),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Err(e) => return Err(e.to_string()),
        }
    }
    let bundle = bundle_of(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    std::fs::write(&path, plist_xml(&bundle)).map_err(|e| e.to_string())?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_real_bundle_is_accepted() {
        let bundle = bundle_of(Path::new("/Applications/iofast.app/Contents/MacOS/iofast"))
            .expect("an installed bundle resolves");
        assert_eq!(bundle, Path::new("/Applications/iofast.app"));
        // Must-fail cases: a dev binary, a three-deep path that is not a bundle,
        // and a binary sitting somewhere else inside a real bundle.
        assert!(bundle_of(Path::new("/Users/x/iofast/app/src-tauri/target/debug/iofast")).is_err());
        assert!(bundle_of(Path::new("/opt/iofast/Contents/MacOS/iofast")).is_err());
        assert!(bundle_of(Path::new("/Applications/iofast.app/Foo/Bar/iofast")).is_err());
        assert!(bundle_of(Path::new("/iofast")).is_err());
        // Paths that exist only until the volume ejects or the machine reboots.
        assert!(bundle_of(Path::new("/Volumes/iofast/iofast.app/Contents/MacOS/iofast")).is_err());
        assert!(bundle_of(Path::new(
            "/private/var/folders/a/AppTranslocation/UUID/d/iofast.app/Contents/MacOS/iofast"
        ))
        .is_err());
    }

    #[test]
    fn the_plist_opens_the_bundle_at_login() {
        let xml = plist_xml(Path::new("/Applications/iofast.app"));
        assert!(xml.contains("<string>com.iofast.desktop</string>"));
        assert!(xml.contains("<string>/usr/bin/open</string>"));
        assert!(xml.contains("<string>/Applications/iofast.app</string>"));
        assert!(xml.contains("<key>RunAtLoad</key>\n\t<true/>"));
        // Relaunch-after-quit and polling are deliberately absent.
        assert!(!xml.contains("KeepAlive"));
        assert!(!xml.contains("StartInterval"));
        // A path launchd would otherwise read as markup stays one <string>.
        let odd = plist_xml(Path::new("/Volumes/Tools & Apps/iofast.app"));
        assert!(odd.contains("<string>/Volumes/Tools &amp; Apps/iofast.app</string>"));
        assert!(!odd.contains("& Apps"));
    }

    #[test]
    fn a_plist_for_a_different_copy_reads_as_off() {
        let here = Path::new("/Applications/iofast.app");
        assert!(enabled_in(&plist_xml(here), here));
        // The app was moved: the stale plist would open nothing.
        assert!(!enabled_in(&plist_xml(Path::new("/Users/x/Desktop/iofast.app")), here));
        assert!(!enabled_in("", here));
    }
}
