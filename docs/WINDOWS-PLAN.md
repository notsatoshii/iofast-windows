# iofast for Windows: plan and shared contract

Fork of inputoutput9/iofast (private, James). Goal: on Windows, press a hotkey on whatever
the browser shows (any website, a YouTube video, a GitHub repo, an Instagram or X post or
profile) and get the same iofast output: clean markdown files with the `SOURCE:` line and
UNTRUSTED banner, `manifest.json`, `report.md`, `README-UNTRUSTED.md`, the combined one-file
version, and the three ways to use a run (open in agent, MCP index, API push).

## Steps

1. **Windows shell.** The Tauri app builds and runs on Windows. Global hotkey (default
   Ctrl+Shift+Space; Ctrl+Space is held by another app on Eric's PC; changeable in settings) opens the panel prefilled with the URL of the active
   browser tab (Chrome, Edge, Brave, Firefox, read through Windows UI Automation), falling back
   to the clipboard. The Windows items in PLAN.md are done: MAX_PATH-safe filenames, stdio in
   the windowed frozen engine, tray notifications, keys in Windows Credential Manager, "open in
   agent" through Windows Terminal (or cmd), folder picker, start with Windows.
2. **Any website.** Panel offers "this page" or "whole site" for any URL, not only docs.
3. **YouTube and GitHub.** Video: title, channel, date, description, chapters, transcript
   (with timestamps; manual captions preferred over auto, user's language first, then
   English). Repo: README, file tree, source files (text only, size caps, .gitignore and
   binaries skipped) as one readable corpus, plus repo metadata.
4. **Instagram and X.** Read through a dedicated browser profile the user signs into once by
   hand, using the browser already installed (Edge or Chrome via Playwright `channel`). Reads
   what a signed-in person sees: a post, a thread, or a profile's latest N items. Hard rules:
   no calls to private/undocumented APIs, no stealth or fingerprint patches, no CAPTCHA
   solving, polite pacing; if a challenge or login wall appears, stop and tell the user.
5. **Installer.** NSIS `.exe` installer from `pnpm tauri build`, tray icon, autostart option.

## Shared engine contract (all workers code against this)

New package `engine/src/iofast_engine/sources/`:

```python
# sources/base.py
@dataclass
class GrabOptions:
    mode: str = "page"          # "page" | "site"  (site only meaningful for source "web")
    limit: int | None = None    # max items for profiles/feeds (instagram, x)
    lang: str | None = None     # preferred transcript language (youtube)

@dataclass
class GrabResult:
    source: str                 # "web" | "youtube" | "github" | "instagram" | "x"
    run_dir: Path               # the run folder written, same layout as a docs scrape
    ok: int; thin: int; failed: int

class Source(Protocol):
    name: str
    def matches(self, url: str) -> bool: ...
    async def grab(self, url: str, out_root: Path, opts: GrabOptions, emit) -> GrabResult: ...
```

`sources/__init__.py` holds the registry: `detect(url) -> Source` checks youtube, github,
instagram, x, then falls back to web (docs sites keep using the existing docs pipeline when
`mode == "site"` and a docs platform is detected). Every source writes through the existing
writer/ledger/manifest so runs look the same as docs runs.

New CLI: `iofast grab <url> [--site] [--limit N] [--lang ko] --out <dir>`.

New RPC methods (documented in docs/RPC.md):
- `detect {url}` -> `{source, label, modes: ["page","site"] | ["page"], needs_signin: bool}`
- `grab {url, mode, limit?, lang?, out?}` -> same progress events and final result as `scrape`
- `social.signin {site: "instagram"|"x"}` -> opens a visible browser on the dedicated profile;
  resolves `{signed_in: bool}` when the window closes
- `social.status {}` -> `{instagram: bool, x: bool}`

## Branches

- `win/shell`  step 1 + 5 (app, packaging, Windows fixes)
- `src/web-yt-gh` steps 2 + 3 (sources package, registry, CLI, RPC)
- `src/social` step 4 (instagram.py, x.py, social_browser.py, signin RPC)
