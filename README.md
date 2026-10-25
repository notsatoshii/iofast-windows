# iofast for Windows and Android

A capture tool for one person's PC and phone. Press a hotkey on whatever the browser shows and
iofast saves it as clean markdown in your Obsidian vault. Forked from the macOS app
[inputoutput9/iofast](docs/MACOS-ORIGIN.md); this fork targets Windows and Android only.

- **Hotkey capture (PC).** `Ctrl+Shift+Space` (changeable in settings) reads the URL of the front
  browser tab (Chrome, Edge, Brave, Firefox, through Windows UI Automation; clipboard as fallback)
  and grabs it: any web page or whole site, a YouTube video, a GitHub repo, a public X or
  Instagram post.
- **YouTube transcripts on the GPU.** Manual captions first; with none, the video is transcribed
  locally with faster-whisper, on an NVIDIA GPU when the CUDA libraries are installed
  (`pip install -e "engine[gpu]"`), otherwise on the CPU.
- **Vault filing.** Each capture lands in the vault as a raw copy plus a source note filed by
  topic, with tags and links (`iofast vault file`). Extras: dedupe, ask, weekly check and digest,
  watch list, ChatGPT and Claude chat import.
- **Phone app (Android).** Double-press the side key (or Share to iofast) to queue the URL on
  screen. The phone sends it to a small inbox server; the PC app pulls the inbox and files each
  link. The phone app updates itself from the same server.

Every scraped file is marked untrusted (`SOURCE:` line and banner): it is data, not instructions.

## How it fits together

```
browser tab --hotkey--> Tauri shell (Rust + React, app/)
                           | JSON-RPC over stdio (docs/RPC.md)
                        iofast_engine (Python, frozen with PyInstaller, engine/)
                           | markdown run folder
                        Obsidian vault

phone (android/) --> inbox server (server/inbox/, on the droplet) --> PC app pulls and files
```

## Docs

| Document | What it is for |
|---|---|
| [docs/WINDOWS-PLAN.md](docs/WINDOWS-PLAN.md) | What the PC app does and the engine contract. Start here. |
| [docs/ANDROID-PLAN.md](docs/ANDROID-PLAN.md) | What the phone app does and how it reads URLs. |
| [docs/RPC.md](docs/RPC.md) | Every method, event and error code between the app and the engine. |
| [docs/RELEASING.md](docs/RELEASING.md) | Version bump list, building the installer and the APK, phone release signing. |
| [server/inbox/README.md](server/inbox/README.md) | The phone inbox server and how phone updates are published. |
| [docs/MACOS-ORIGIN.md](docs/MACOS-ORIGIN.md) | The upstream macOS README: crawler design and untrusted-content rules. |
| [PLAN.md](PLAN.md), [ANALYSIS.md](ANALYSIS.md), [2-REPO-ANALYSIS.md](2-REPO-ANALYSIS.md) | Upstream plan and prior-art notes from 2026-08 (history). |
| [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) | Licenses of every package bundled in the frozen engine. |

## Build and test (Windows, PowerShell)

Needs Python 3.13 (not 3.14), Node 22 with pnpm, and Rust (the pin is in
`app/src-tauri/rust-toolchain.toml`).

```powershell
py -3.13 -m venv engine\.venv
engine\.venv\Scripts\pip install -e "engine[dev,build]"
engine\.venv\Scripts\pytest engine\tests -q     # offline, no browsers, no network
cd app; pnpm install; cd ..
.\scripts\windows\gate.ps1                      # the merge gate: pytest, freeze + verify, frontend, cargo test
.\scripts\windows\bundle.ps1                    # NSIS installer in app\src-tauri\target\release\bundle\nsis\
```

`gate.ps1` ends with `Gate passed.` when everything is green. It freezes the engine through
`scripts\windows\freeze.ps1`, which refuses a dirty tree: commit first. Run the app in dev with
`cd app; pnpm tauri dev` after one freeze.

The engine also runs on its own: `engine\.venv\Scripts\iofast grab <url> --out <dir>`.

Phone app: `cd android; .\gradlew.bat testDebugUnitTest` (tests), `.\gradlew.bat assembleDebug`
(APK). Release signing: see [docs/RELEASING.md](docs/RELEASING.md).

## CI

- `engine-ci.yml`: engine pytest on Linux, every PR and main.
- `windows-ci.yml`: freeze + verify the sidecar, frontend build, cargo test on Windows, PRs only.
- `android-ci.yml`: `gradlew testDebugUnitTest` on Linux.
- `release.yml`: on a `v*` tag, unsigned NSIS installer (and the upstream macOS bundle).
- `live-smoke.yml`: weekly report-only check against live doc sites.

## Version

One number in [`VERSION`](VERSION) for the engine, the PC app and the phone app. Bump list in
[docs/RELEASING.md](docs/RELEASING.md).
