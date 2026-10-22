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
4. **Instagram and X.** Public-first capture (no login) for posts, Reels, and threads via
   the same endpoints the platforms' own logged-out web pages and embed widgets use. Falls
   back to a dedicated browser profile (signed in once by hand, Edge or Chrome via Playwright
   `channel`) only for private/gated content, Stories, and profiles. Hard rules: public
   content only on the public route, handful of requests per capture, polite back-off on
   429/5xx, never retry-hammer; no proxy rotation, no fingerprint spoofing, no CAPTCHA
   solving, no automated login. **Freeze note:** the frozen build must include `httpx`
   (used by `sources/social_public.py` and `watch/checker.py`); add it to the PyInstaller
   `hiddenimports` or `collect_all` list in `freeze.ps1`.
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

## Step 6: Obsidian filing

After any capture, one call files it into Eric's Obsidian vault:

1. **Vault scan** — reads folders, note titles/aliases, tag vocabulary, MOC notes (read-only).
2. **Raw copy** — `Research (RAW)/iofast/<YYYY-MM-DD> <Title>.md` with frontmatter (source url, type, captured date, run path) and UNTRUSTED banner. Never overwrites; appends `-2`, `-3` on collision. Caps at 200k chars inline, links to full run beyond that.
3. **LLM classification** — returns structured JSON only (no tools): topic folder, tags (kebab-case, max 8, reuses vocabulary), aliases, source note (summary, key points, quotes, entities as `[[links]]`), up to 8 new entity notes, append-sections for existing notes, one MOC line. Backends: local `claude` CLI (preferred, `--output-format json --tools "" --strict-mcp-config`, cwd = `%LOCALAPPDATA%\iofast\llm-cwd`) or OpenRouter API.
4. **Engine writes** — source note in topic folder, new entity notes, appended sections to existing notes (never modifies Eric's text), MOC line appended, log line appended. All paths resolved and verified inside vault.
5. **Lint** — deterministic (no LLM): broken `[[links]]`, orphans, missing frontmatter, singleton tags, MOC drift. `--fix` only repairs MOC index drift.

CLI: `iofast vault file <run_dir> [--vault PATH] [--dry-run]`, `iofast vault lint [--vault PATH] [--fix]`, `iofast vault find`.
RPC: `vault.status`, `vault.file {run_dir, dry_run?, vault?, backend?}`, `vault.lint {vault?, fix?}`.

Module: `engine/src/iofast_engine/vault/` (scan.py, detect.py, raw.py, classify.py, writer.py, filer.py, lint.py, vault_rpc.py).
CLI hook: `engine/src/iofast_engine/cli_vault.py` (standalone, merged into cli.py by the integration branch).

## Step 7: Vault extras

Post-filing tools that run on the vault itself, not on a capture run:

1. **Dedupe** — URL normalization across platforms (YouTube shorts/youtu.be/mobile, twitter→x.com, instagram reel→p), content hashing, whitespace-insensitive title matching. Runs before filing to skip duplicates; also available standalone.
2. **Ask** — BM25 retrieval over note chunks with title/alias/tag boosts, incremental mtime-based index (`.iofast-ask-index.json`), LLM answer with `[[Note Title]]` citations. Optional `--save` writes the answer as a vault note.
3. **Check** — weekly vault health report: lint issues, likely duplicates, Quicknotes triage, singleton tags, MOC drift. Writes `Weekly/<YYYY>-W<ww> Vault check.md`. Idempotent per week.
4. **Digest** — weekly research digest from log entries and source notes. LLM synthesizes themes and highlights. Writes `Weekly/<YYYY>-W<ww> Digest.md`. Idempotent per week.

CLI: `iofast vault ask <question> [--save]`, `iofast vault check [--week YYYY-Www]`, `iofast digest [--week YYYY-Www] [--if-due]`.
RPC: `vault.ask {question, vault?, top_k?, save?}`, `vault.check {vault?, week?}`, `digest.make {vault?, week?}`.

Module: `engine/src/iofast_engine/vault/` (dedupe.py, ask.py, check.py, digest.py, extras_rpc.py).
CLI hook: `engine/src/iofast_engine/cli_vault_extras.py`.

Bug fixes in this step: title extraction now checks manifest `info.title` then H1 headings then host from URL; entity links filtered to only existing or newly-created notes.

## Branches

- `win/shell`  step 1 + 5 (app, packaging, Windows fixes)
- `src/web-yt-gh` steps 2 + 3 (sources package, registry, CLI, RPC)
- `src/social` step 4 (instagram.py, x.py, social_browser.py, signin RPC)
- `feat/intake` step 7a (watch list + chat history import)
- `feat/vault-extras` step 7b (dedupe, ask, check, digest, filing bug fixes)

## Step 7a: Intake (watch list + chat import)

### Watch list

Monitor URLs for new content on a daily or weekly schedule. Stored in
`%APPDATA%\iofast\watch.json` (Windows) or `~/Library/Application Support/iofast/watch.json`
(macOS). Each entry auto-detects kind from URL: YouTube channel/playlist (yt-dlp flat
listing), GitHub repo (releases + README SHA), website (RSS/Atom if linked, else sitemap
lastmod, else page text hash), X/Instagram profile (requires sign-in via existing social
browser; skips with clear message if not signed in).

First run records a baseline and captures nothing unless `--backfill N`. Subsequent runs
surface only genuinely new items. One failing entry never stops others. New items go through
normal `grab` and optionally vault filing when the entry has `--file`. Results logged to
`Research (RAW)/iofast/log.md` when filing. Max 10 new items per run, human pacing.

CLI: `iofast watch add <url> [--every daily|weekly] [--file] [--limit N]`,
`iofast watch list`, `iofast watch remove <id>`, `iofast watch run [--if-due] [--backfill N]`.
RPC: `watch.add`, `watch.list`, `watch.remove`, `watch.run`.

Module: `engine/src/iofast_engine/watch/` (store.py, checker.py, runner.py, watch_rpc.py).
CLI hook: `engine/src/iofast_engine/cli_watch.py`.

### Chat history import

Import personal chat exports into markdown. No UNTRUSTED banner — these are Eric's own
chats and never leave the chosen LLM backend. Raw copies go to
`Research (RAW)/AI chats/<ChatGPT|Claude>/`.

- **ChatGPT**: parses `conversations.json` mapping tree, follows final branch only, skips
  system messages. Each conversation → one markdown file with title, date, speaker labels.
- **Claude**: parses `conversations.json` chat_messages list. Same output format.

Dry run prints counts (conversations, messages, date range, trivial skips for <4 messages
or <300 chars) and LLM-call estimate. Flags: `--since YYYY-MM-DD`, `--limit N`,
`--min-messages N`. Resumable via progress file (re-running skips done conversation IDs).

CLI: `iofast import chatgpt <export.zip>`, `iofast import claude <export.zip>`.
RPC: `import.chatgpt`, `import.claude`.

Module: `engine/src/iofast_engine/import_chat/` (chatgpt.py, claude.py, writer.py, import_rpc.py).
CLI hook: `engine/src/iofast_engine/cli_import.py`.
