//! Start-at-login (decision D3). macOS uses a LaunchAgent plist; Windows uses
//! the HKCU Run registry key. Both launch THE APP at login so the resident
//! scheduler in `schedule.rs` is actually running when a run's daily/weekly
//! re-scrape falls due. Scope is autostart only: no headless twin, no second
//! scraping process, so nothing new has to be locked.

/// Registry value name under HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run.
const REG_VALUE: &str = "iofast";

#[cfg(target_os = "macos")]
mod macos {
    use std::path::{Path, PathBuf};

    const LABEL: &str = "com.iofast.desktop";

    pub(super) fn plist_path() -> Result<PathBuf, String> {
        let home = std::env::var_os("HOME").ok_or("no HOME")?;
        Ok(PathBuf::from(home)
            .join("Library/LaunchAgents")
            .join(format!("{LABEL}.plist")))
    }

    /// `…/iofast.app/Contents/MacOS/iofast` -> `…/iofast.app`.
    pub(super) fn bundle_of(exe: &Path) -> Result<PathBuf, String> {
        let macos = exe.parent();
        let bundle = macos
            .and_then(Path::parent)
            .and_then(Path::parent)
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

    pub(super) fn plist_xml(app: &Path) -> String {
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

    pub(super) fn enabled_in(text: &str, app: &Path) -> bool {
        text.contains(&format!("<string>{}</string>", xml_escape(&app.to_string_lossy())))
    }
}

#[cfg(target_os = "windows")]
mod windows {
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;

    const REG_KEY: &str = "Software\\Microsoft\\Windows\\CurrentVersion\\Run";

    /// Encode an OS string as a null-terminated wide string for Win32 APIs.
    fn to_wide(s: &OsStr) -> Vec<u16> {
        s.encode_wide().chain(std::iter::once(0)).collect()
    }

    /// Read the current value of our Run key entry, if any.
    pub(super) fn reg_value() -> Option<String> {
        unsafe {
            use windows_sys::Win32::System::Registry::{
                RegCloseKey, RegOpenKeyExW, RegQueryValueExW, HKEY_CURRENT_USER, KEY_READ,
                REG_SZ,
            };
            let key_wide = to_wide(OsStr::new(REG_KEY));
            let val_wide = to_wide(OsStr::new(super::REG_VALUE));
            let mut hkey: windows_sys::Win32::System::Registry::HKEY = std::ptr::null_mut();
            if RegOpenKeyExW(HKEY_CURRENT_USER, key_wide.as_ptr(), 0, KEY_READ, &mut hkey) != 0 {
                return None;
            }
            let mut buf_len: u32 = 0;
            let mut kind: u32 = 0;
            // First call with null buffer to get the size.
            let rc = RegQueryValueExW(
                hkey,
                val_wide.as_ptr(),
                std::ptr::null_mut(),
                &mut kind,
                std::ptr::null_mut(),
                &mut buf_len,
            );
            if rc != 0 || kind != REG_SZ {
                let _ = RegCloseKey(hkey);
                return None;
            }
            let mut buf: Vec<u16> = vec![0; (buf_len / 2) as usize];
            let rc = RegQueryValueExW(
                hkey,
                val_wide.as_ptr(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                buf.as_mut_ptr() as *mut u8,
                &mut buf_len,
            );
            let _ = RegCloseKey(hkey);
            if rc != 0 {
                return None;
            }
            // Strip trailing nulls.
            while buf.last() == Some(&0) {
                buf.pop();
            }
            Some(String::from_utf16_lossy(&buf))
        }
    }

    /// Set or delete the Run key entry. The value is the quoted exe path so
    /// paths with spaces survive.
    pub(super) fn set_reg(enabled: bool, exe: &std::path::Path) -> Result<(), String> {
        unsafe {
            use windows_sys::Win32::System::Registry::{
                RegCloseKey, RegDeleteValueW, RegOpenKeyExW, RegSetValueExW,
                HKEY_CURRENT_USER, KEY_SET_VALUE, KEY_WRITE, REG_SZ,
            };
            let key_wide = to_wide(OsStr::new(REG_KEY));
            let val_wide = to_wide(OsStr::new(super::REG_VALUE));
            let mut hkey: windows_sys::Win32::System::Registry::HKEY = std::ptr::null_mut();
            let access = if enabled { KEY_WRITE } else { KEY_SET_VALUE };
            if RegOpenKeyExW(HKEY_CURRENT_USER, key_wide.as_ptr(), 0, access, &mut hkey) != 0 {
                return Err("could not open Run registry key".into());
            }
            if !enabled {
                let rc = RegDeleteValueW(hkey, val_wide.as_ptr());
                let _ = RegCloseKey(hkey);
                // ERROR_FILE_NOT_FOUND (2) means already gone — that is fine.
                if rc != 0 && rc != 2 {
                    return Err("could not delete autostart entry".into());
                }
                return Ok(());
            }
            // Quote the path so spaces are safe.
            let data = format!("\"{}\"", exe.display());
            let data_wide = to_wide(OsStr::new(&data));
            let byte_len = (data_wide.len() * 2) as u32;
            let rc = RegSetValueExW(
                hkey,
                val_wide.as_ptr(),
                0,
                REG_SZ,
                data_wide.as_ptr() as *const u8,
                byte_len,
            );
            let _ = RegCloseKey(hkey);
            if rc != 0 {
                return Err("could not write autostart entry".into());
            }
            Ok(())
        }
    }
}

#[tauri::command]
pub fn autostart_get() -> Result<bool, String> {
    #[cfg(target_os = "macos")]
    {
        use macos::*;
        let Ok(bundle) = std::env::current_exe()
            .map_err(|e| e.to_string())
            .and_then(|e| bundle_of(&e))
        else {
            return Ok(false);
        };
        let text = std::fs::read_to_string(plist_path()?).unwrap_or_default();
        Ok(enabled_in(&text, &bundle))
    }
    #[cfg(target_os = "windows")]
    {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let quoted = format!("\"{}\"", exe.display());
        Ok(windows::reg_value().as_deref() == Some(quoted.as_str()))
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    Ok(false)
}

#[tauri::command]
pub fn autostart_set(enabled: bool) -> Result<bool, String> {
    #[cfg(target_os = "macos")]
    {
        use macos::*;
        let path = plist_path()?;
        if !enabled {
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
    #[cfg(target_os = "windows")]
    {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        windows::set_reg(enabled, &exe)?;
        Ok(enabled)
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = enabled;
        Err("autostart not supported on this platform".into())
    }
}

#[cfg(test)]
mod tests {
    #[cfg(target_os = "macos")]
    mod macos_tests {
        use super::super::macos::*;
        use std::path::Path;

        #[test]
        fn only_a_real_bundle_is_accepted() {
            let bundle = bundle_of(Path::new("/Applications/iofast.app/Contents/MacOS/iofast"))
                .expect("an installed bundle resolves");
            assert_eq!(bundle, Path::new("/Applications/iofast.app"));
            assert!(bundle_of(Path::new("/Users/x/iofast/app/src-tauri/target/debug/iofast")).is_err());
            assert!(bundle_of(Path::new("/opt/iofast/Contents/MacOS/iofast")).is_err());
            assert!(bundle_of(Path::new("/Applications/iofast.app/Foo/Bar/iofast")).is_err());
            assert!(bundle_of(Path::new("/iofast")).is_err());
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
            assert!(!xml.contains("KeepAlive"));
            assert!(!xml.contains("StartInterval"));
            let odd = plist_xml(Path::new("/Volumes/Tools & Apps/iofast.app"));
            assert!(odd.contains("<string>/Volumes/Tools &amp; Apps/iofast.app</string>"));
            assert!(!odd.contains("& Apps"));
        }

        #[test]
        fn a_plist_for_a_different_copy_reads_as_off() {
            let here = Path::new("/Applications/iofast.app");
            assert!(enabled_in(&plist_xml(here), here));
            assert!(!enabled_in(&plist_xml(Path::new("/Users/x/Desktop/iofast.app")), here));
            assert!(!enabled_in("", here));
        }
    }
}