# Releasing

## Version number

`VERSION` at the repo root is the one version for the PC app, the engine and the phone app.

To bump, change these together in one commit:

| File | What reads it |
|---|---|
| `VERSION` | Gradle (`versionName`), the engine at runtime (`iofast_engine.__version__`, sent as `engine` in the RPC `initialize` reply), `iofast.spec` (stamped into the frozen build) |
| `engine/pyproject.toml` `version` | pip metadata. `iofast.spec` refuses to freeze if it differs from `VERSION` |
| `app/src-tauri/Cargo.toml` `version` | the Tauri app and the NSIS installer name (`tauri.conf.json` has no version on purpose). `build.rs` fails the build if it differs from `VERSION`. `Cargo.lock` updates itself on the next build: commit it |
| `app/package.json` `version` | nothing at runtime; kept equal for clarity |
| `android/app/build.gradle.kts` `versionCode` | raise by 1 on every phone release. Phones only update when this number goes up (`UpdateWorker`) |

`engine/tests/test_version_sync.py` checks all of the above except `versionCode`.

## PC app (Windows)

1. `.\scripts\windows\gate.ps1` passes on a clean, committed tree.
2. `.\scripts\windows\bundle.ps1` builds the NSIS installer in
   `app\src-tauri\target\release\bundle\nsis\`.
3. A `v*` tag also builds the installer in CI (`release.yml`, job `windows`) as an unsigned artifact.

## Phone app (Android)

1. Release signing needs `IOFAST_STORE_FILE`, `IOFAST_STORE_PASSWORD`, `IOFAST_KEY_ALIAS` and
   `IOFAST_KEY_PASSWORD` (Gradle property or environment variable). Without them
   `assembleRelease` stops with an error; it no longer falls back to the debug keystore.
   Android only installs an update signed with the same key as the installed app. If the
   phones run a debug-key build, the first real-key build must be installed by hand
   (uninstall first). To keep shipping debug-key builds on purpose, set `IOFAST_SIGN_WITH_DEBUG=1`.
2. `cd android; .\gradlew.bat assembleRelease`
3. Upload per `server/inbox/README.md`: the APK as `app/phone.apk` and
   `{"versionCode","versionName","sha256","size"}` as `app/phone.json`.
