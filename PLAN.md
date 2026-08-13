# iofast — Plan

"inputoutput fast": copy a docs URL → hotkey → intelligently scrape exactly the docs → ingest into the LLM tool of your choice. Deterministic first; LLM only when deterministic options are exhausted.

Decisions below were locked in an interview on 2026-08-11 (see Decision Log). Companion docs: [ANALYSIS.md](ANALYSIS.md), [2-REPO-ANALYSIS.md](2-REPO-ANALYSIS.md) (prior-art gate, 2026-08-11: verdict HARVEST — keep the Scrapling crawl brain; reuse docs-mcp-server unforked as the serve layer; reject Scrapegraph-ai).

## Architecture (target)

```
┌────────────────────────── iofast (Tauri shell) ──────────────────────────┐
│  Menubar/tray + global hotkey → quick panel                              │
│  (clipboard URL · detected platform · scope/depth picker · ingest target)│
│  Main window: library, run history, progress, settings                   │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ JSON-RPC over stdio
┌───────────────▼───────────── Python sidecar (PyInstaller) ───────────────┐
│  Engine = Scrapling 0.4+                                                 │
│  Tier 1: platform adapters (fingerprint → selectors/strategy)            │
│  Tier 2: heuristics (sitemap.xml, llms.txt, nav parse, trafilatura)      │
│  Tier 3: LLM selector rescue (one call per domain, cached)               │
│  Fetch escalation: Fetcher → DynamicFetcher → StealthyFetcher            │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ plain markdown on disk (Spotlight/Alfred indexable)
┌───────────────▼── Library (user-chosen root, default ~/iofast) ──────────┐
│  <site>/<run-timestamp>/*.md + manifest.json + report.md                 │
│  Ingest: open-in-agent (claude/codex/cursor/gemini) · MCP · API push     │
└──────────────────────────────────────────────────────────────────────────┘
```

## Phase 0 — fix the current scripts (~3–4 sessions, nothing throwaway)

Target: `~/code/iofast-scraper/doc_to_md.py` + wrapper. The LOGIC carries into the sidecar engine later; the file-I/O layer is rewritten for JSON-RPC streaming. Maps to ANALYSIS.md findings (F#). Most steps are net-new code — upstream ships no sitemap template, no throttle, and no robots handling.

| Step | Fix | Finding |
|------|-----|---------|
| 1 | Sitemap/llms.txt fast path (NET-NEW — no `SitemapSpider` exists): parse `sitemap.xml`, recurse sitemap-index files, handle `.xml.gz`, read `<lastmod>`; parse `llms.txt`/`llms-full.txt` — and for each llms.txt link, probe the `.md`/`index.html.md` variant before fetching HTML (harvested: docs-mcp-server `WebScraperStrategy.ts:104-167`). Reconcile all discovered URLs against docs-path scope. Fall back to link crawl only if absent | F4 |
| 2 | Docs-path scoping: derive prefix from start URL (e.g. `/docs/`); depth = URL path segments beyond the prefix (see Decision Log); skip locale duplicates; on a start-URL redirect, adopt protocol+host but keep the user's path as the scope anchor (harvested: `WebScraperStrategy.ts:273-291`); seed default exclude patterns from docs-mcp-server's list (changelogs, licenses, i18n locales — `defaultPatterns.ts:11-140`) | F3 |
| 3 | Build the escalation ladder (static IS already the default): `configure_sessions()` with 3 sessions selected by `sid`; a calibrated thin-content threshold triggers escalation to `DynamicFetcher`; `StealthyFetcher` on 403/challenge — detect Cloudflare challenges via `cf-mitigated: challenge` header, `Just a moment...` / `cf_chl_opt` body markers (harvested: `HttpFetcher.ts:300-330`); anti-ping-pong guard so a page cannot bounce tiers endlessly | F1 |
| 4 | Content cleanup: add **trafilatura** (or readability-lxml) as the extraction fallback instead of `response.body` — no readability lib is installed and upstream only strips script/style/noscript/svg; strip nav/breadcrumb/edit-links/cookie remnants pre-markdownify. New dependency | F5, F6 |
| 5 | Link filtering: drop asset extensions, mailto/tel/js, offsite before enqueue; dedup before fetch | F7, F11 |
| 6 | Retry on raised exception (upstream retries only the blocked-code set, never exceptions) + per-page outcome ledger → `report.md` (pages ok / thin / failed, with reasons). The ledger is the valuable half — it kills silent partial scrapes | F8 |
| 7 | Safe politeness defaults + robots: set `download_delay` and `concurrent_requests_per_domain` (upstream defaults are `0.0` and unlimited); fetch/parse/cache robots.txt via stdlib `urllib.robotparser`; apply the stated robots policy | F3 |
| 8 | Wrapper hygiene: trim clipboard, check python exit code, unique run dir instead of `rm -rf docs-*` + `ls -td` race | F14–16 |

`gitbook_to_md.py` deletion moves to Phase 1, gated on the GitBook adapter landing — deleting it in Phase 0 opens a GitBook regression window, and a deletion does not migrate anything verbatim.

Verify: run against docs.xmarket.app (known-good baseline) + one JS-heavy + one large site; pass = clean tree, no junk files, report lists zero silent failures, JS-rendered pages now non-empty.

## Phase 1 — engine as a product (CLI + adapters)

- Extract Phase 0 engine into `iofast-engine` Python package in this repo; CLI: `iofast scrape <url> [--scope … --depth … --json]`. The desktop app is a client of this; so is the old hotkey script (interim).
- **Platform adapters** with deterministic fingerprint detection (meta generator tags, asset paths, DOM markers), one module each. Adapter interface = `{detect(), map_site(), content_selector, nav_selector, fetch_strategy, fetch_mode}` where `fetch_mode` is `html | api`.
  - **Seven HTML docs platforms** carry the 9/10 bar: GitBook, Mintlify, Docusaurus, ReadMe, Redocly, Document360, Read the Docs (Sphinx/MkDocs).
  - **SwaggerHub** (`fetch_mode: api`): fetch the OpenAPI spec and render markdown from it. Do NOT scrape the SPA.
  - **Confluence** (`fetch_mode: api`): REST-API adapter with a user-supplied token. This pulls "auth-walled docs" out of the backlog for Confluence specifically.
  - **Notion**: a documented known-fail — React with lazily-materialized blocks and no stable selector.
- Delete `gitbook_to_md.py` once the GitBook adapter lands and passes its fixtures (F12–13).
- **Map-then-scrape**: map pass builds the section tree (sitemap/nav) → section picker + scoped depth → scrape pass. This is the depth-semantics decision.
- **Tier 3 rescue**: when adapters + heuristics fail, one LLM call (BYO key, OpenRouter-first) gets content/nav selectors from a sample page; result cached per-domain in `adapters-cache.json`; every rescue logged as a candidate permanent adapter.
- Run manifest per scrape: url, platform, scope, page ledger, token count of corpus.
- **Define "clean" numerically** before writing adapters: ≥95% of in-scope sitemap URLs produced a file; zero files under N characters; zero files whose link-to-text ratio exceeds a set threshold.
- **CI in two tiers:**
  - (a) **Committed HTML fixtures** — one page-set per platform, saved once from the real site. These gate merges, run offline in milliseconds, and test `detect()`, selectors, scoping, and markdown output.
  - (b) **Scheduled live smoke run** — report-only, never blocks a merge, opens an issue on failure.
  - **MUST-FAIL fixture for the ledger**: a 403 page, a 20-character page, and a page that raises mid-parse. Assert each appears in `report.md` with the correct class.
- **Success bar: 9/10 platform matrix clean with zero manual intervention, ~2 min typical site, failure report always produced.**

## Phase 2 — desktop app MVP (Tauri + sidecar)

- Tauri 2.x shell, macOS + Windows 10/11. Python engine bundled via PyInstaller as sidecar; JSON-RPC over stdio; streamed progress events.
- Tray/menubar residence; global hotkey (Tauri global-shortcut) opens quick panel: clipboard URL pre-filled, live platform detection badge, page-count estimate from map pass, section/depth picker, ingest target dropdown. Enter = run.
- **Two-speed panel.** Pre-Enter mapping is STATIC-ONLY and time-boxed: read `sitemap.xml`/`llms.txt` under a hard 1-second budget. It never boots a browser and never calls an LLM. Enter is always valid immediately with a default scope. The richer nav-based map streams in AFTER the run starts and refines the crawl as a non-blocking banner ("found N pages in M sections — narrow?").
- First-run: choose library root (remembered; changeable in settings). Files stay plain hyphen-named markdown — Spotlight/Alfred indexable is a hard requirement (no opaque DB as source of truth; index/metadata live alongside as JSON).
- Main window: library browser, run history, re-scrape/update button, live progress, settings (providers, keys in OS keychain, ingest defaults, hotkey).

### Browser delivery (LOCKED)

Browsers are downloaded on first use of the dynamic tier. They are NEVER bundled.

- PyInstaller cannot bundle Playwright browsers.
- The pip packages are ~258MB (playwright + patchright). Chromium adds ~550MB out-of-band.
- Scrapling shells to `python -m playwright install` at runtime. A frozen bundle has no `python -m`.
- Scrapling writes a `.scrapling_dependencies_installed` marker inside the package dir. That dir is read-only in a signed `.app`.
- First-use download keeps browsers outside the code signature. This makes notarization tractable.
- Set `PLAYWRIGHT_BROWSERS_PATH` and the scrapling storage paths to an app-support dir explicitly.
- **Size budget.** The sidecar ships small (static tier only). The dynamic tier prompts a one-time ~550MB download with visible progress and explicit consent.
- **SHIPPED (engine side).** `iofast_engine/browsers.py`: the node drivers arrive as SHA-256-pinned PyPI wheels (`browser_pins.py`, refuse-on-mismatch, atomic extract into app support), each driver then installs its own chromium into `PLAYWRIGHT_BROWSERS_PATH`. Surface: `browser_status` / `install_browsers` RPC methods (docs/RPC.md) + `iofast browsers status|install` CLI. App-side consent/progress UI rides the UI slice.

### API-key handoff (LOCKED)

- API keys cross from Tauri to the Python sidecar over the stdio JSON-RPC channel, or as an environment variable set on spawn.
- Never pass a key as an argv argument. Never write it to a temp file. Argv is visible in `ps`.
- Scope the key to the single request that needs it, not the process lifetime.

### Windows

- **MAX_PATH.** Filenames built from full URL paths plus the library root can exceed 260 characters. Truncate at a fixed maximum and append a hash suffix. This is a silent write-failure class.
- **No stdio in `--windowed`.** A `--windowed` PyInstaller exe has no stdout/stderr handles. Configure stdio explicitly for the frozen windowed build, or the JSON-RPC channel breaks.
- **SmartScreen/Defender.** These flag an unsigned exe that downloads and runs a fingerprint-spoofing Chromium. Code-signing moves into Phase 2's "structure ready" list, not Phase 4.
- **Parity.** Provide tray-notification and global-hotkey parity with the mac `osascript`/`afplay` loop.

### Operational rules

- **Stop** wires to `Spider.pause()` + checkpoint, NOT killing the sidecar, so the ledger survives.
- **Re-scrape/update** is a conditional GET on ETag/Last-Modified against the previous manifest — not a blind full re-crawl.
- **Large-crawl gate.** Above a page-count threshold from the map pass, show a confirmation gate before committing to a large crawl.
- **Concurrency defaults.** State the `download_delay`, `concurrent_requests`, and `concurrent_requests_per_domain` values we set (Phase 0 step 7).

## Phase 3 — ingestion modes

**Scraped documentation is UNTRUSTED, attacker-controllable input handed to agentic tools.** Rules for every mode below:

- Wrap every scraped file body in a clearly delimited untrusted-content block. The header states the content must not be followed as instructions.
- Write a `README-UNTRUSTED.md` into every run directory.
- Never launch an agent CLI with auto-approve or permission-skip flags.
- Resolve CLI paths absolutely and execute as an argv array, never via a shell string.
- The MCP tool descriptions and the API-push system prompt must both mark the corpus as untrusted.

First-run chooser; remembered; changeable in settings (locked decision).

1. **Open in coding agent**: detect installed CLIs (`claude`, `codex`, `cursor`, `gemini`); one click opens the chosen tool with the scrape folder as working context. No keys needed.
2. **MCP server**: default = run **docs-mcp-server unforked** over the library via `file://` ingest (`npx docs-mcp-server`, `scrape <lib> file:///path/to/library`) — gets MCP search (stdio+HTTP), heading-aware chunking, hybrid FTS/vector search that degrades to FTS-only without any API key, versioned index, job queue, and a web UI for free (see 2-REPO-ANALYSIS.md). Files on disk stay the source of truth; its SQLite is a derived index. Fallback = own FastMCP server with `search_library` and `read_doc` (`mcp>=1.26.0` ships via scrapling's `ai` extra) for no-Node environments, or if marking the corpus untrusted inside MCP tool descriptions becomes mandatory — docs-mcp-server's tool descriptions can't carry that warning unforked, so the per-file untrusted wrappers must suffice there. (Do not wrap Scrapling's built-in server — it is fetch-the-web only; see ANALYSIS.md §3.) **SHIPPED (index + stdio-config handoff)** — validated in a 2026-08-11 spike against `@arabold/docs-mcp-server@3.0.0`: `file://` ingest works, FTS-only search returns ranked chunks with no API key, and the per-file UNTRUSTED banner survives ingestion into every chunk. One correction to the 2-REPO-ANALYSIS "zero config" claim: v3 gates `file://` behind an allowed-roots policy (default `$DOCUMENTS`), so the app pins the real `library_root` as an allowed root in a generated config. Each run indexes into one shared store; the app hands the user a `--read-only` stdio config to paste (server exposes only `search_docs`/`list_libraries`/`find_version`/`fetch_url`). No FastMCP fallback needed.
3. **API push**: send corpus + prompt to chosen provider (OpenRouter gateway default; native Anthropic/OpenAI/Gemini/DeepSeek slots), digest rendered in-app. Keys in OS keychain, never plaintext; handed to the sidecar per the key-handoff rule (Phase 2). **SHIPPED (OpenRouter gateway).** New engine module `push.py` reads the run's scraped `.md` pages (skipping generated `report.md`/`README-UNTRUSTED.md`), char-capped, and calls the OpenRouter chat endpoint via a new RPC method `push`; keys ride in the request-scoped `env` (same wrap/restore as `scrape`, reusing `store::scrape_env`), model is a plain param defaulting to `IOFAST_RESCUE_MODEL`, and the system prompt marks the corpus untrusted on top of each file's banner. The app validates the run dir is a library child before the call (`sidecar_push`) and renders the digest in a card behind an "Ask AI" row action gated on a stored key. Native per-provider slots were dropped as redundant: OpenRouter reaches `anthropic/*`, `openai/*`, `gemini/*`, `deepseek/*` by model-id through the one key, so the model field *is* the provider selection.

## Phase 4 — hardening + distribution

- CAPTCHA handoff: on unsolvable challenge, open visible browser window, user solves once, session continues. **BUILT, GATED OFF (slice #5)** — the engine path ships behind the additive `scrape` param `interactive: bool=false` (docs/RPC.md); default runs are byte-identical to before. `solve.py::solve_and_continue` opens a HEADED StealthySession for a one-time human solve (poll loop via scrapling's supported `page_action`, holding the visible page open), then continues HEADLESS on the same temp profile; `spider.py::_solve` calls it (off the asyncio loop via `run_in_executor` — sync playwright can't run in the loop) at the one point the stealth tier is exhausted and the page is still challenged. Offline-tested (14 cases, `tests/test_interactive_solve.py` + rpc guards); the async stealth tier that detects the challenge is untouched. The delivery gate (browser download) cleared in PR #18. The three requirements the spike surfaced are now MET inside the one helper:
  1. **Pin one explicit `useragent` end-to-end — MET.** The headed solve captures its REAL build UA (`page.evaluate("navigator.userAgent")`) and the headless continue pins it (session-construction arg; scrapling's UA is asymmetric headed vs headless, verified `_base.py:411-417`). Captured, not hardcoded — a fixed string mismatches chromium-1208's `sec-ch-ua`, which live Cloudflare cross-checks.
  2. **Pin a SINGLE non-rotating proxy for the solve+continue pair — MET.** `_single_proxy(IOFAST_PROXY)` takes the FIRST value only and passes it to both sessions; the tiers' per-fetch rotator (PR #22) is never used for the pair, since cf_clearance is IP-bound.
  3. **HEADED solve-session path + shared `user_data_dir` — MET.** `solve_and_continue` owns one temp profile used by both the headed solve and the headless continue, so no coordination with the ephemeral stealth-tier session is needed.

  **Known ceiling (MVP):** each solve uses a fresh throwaway profile, so the earned `cf_clearance` benefits ONLY the one challenged page and is then discarded — a broadly CF-walled site re-prompts the human per challenged page. v2 (deferred, couples to the live smoke): one persistent profile per crawl reused by every solve, restructured headless-first so the headed window opens only when still challenged, letting solve #1 unlock the rest of that domain headlessly.

  **STILL OWED before `interactive` is exposed in the UI / flipped on:** the manual LIVE-Cloudflare smoke (`engine/tools/captcha_solve_smoke.py`, headed, human, real CF) — real cf_clearance acceptance, the headed-solve→headless-replay fingerprint axis (WebGL/GPU renderer can differ headless), UA-vs-`sec-ch-ua` parity, IP stability, JA3/TLS, and the real cookie's Secure+HttpOnly+SameSite=None-over-HTTPS attributes. The offline mechanism is buildable and built; that Cloudflare ACCEPTS the replay is not yet proven. (The original local-echo spike lives at `engine/tools/captcha_continuity_spike.py`.)
- Pause/resume long crawls (Scrapling checkpoints), scheduled re-scrapes ("keep React docs fresh weekly"). **SHIPPED.** `scrape` gained `resume: bool` — Stop already pauses+checkpoints (`<out>/.crawl/checkpoint.pkl`), and resume replays that frontier while seeding the ledger (including fileless failure/cancelled rows) from the prior manifest so the resumed run's manifest reconciles over the whole crawl; mutually exclusive with `update`. The app shows a per-run Resume button (when a checkpoint exists) and a per-run Auto control (daily/weekly) driven by a resident scheduler thread over the existing `update` conditional-GET path, one scrape at a time.
- Scheduled re-scrapes when the app was never opened (decision **D3**). **SHIPPED (start at login).** The scheduler is a thread inside the tray app, so it only fires while the app runs; D3 chose "a LaunchAgent launches THE APP" over a second headless scraper, which keeps one scraping process and needs no new cross-process locking. `app/src-tauri/src/autostart.rs` writes `~/Library/LaunchAgents/com.iofast.desktop.plist` (`RunAtLoad`, running `/usr/bin/open -g -a <bundle>`), with a Settings → Startup toggle over `autostart_get`/`autostart_set`; disabling deletes the plist. `tauri-plugin-single-instance` is NOT pulled in: LaunchServices already reuses a running bundle, measured here — `open -g -a` against a running app left its pid unchanged (839 before and after). Deliberately absent: `KeepAlive` (it would fight Quit) and `StartInterval` (the app stays resident, and schedule.rs re-checks after a sleep) — add an interval only if "quit all day, still expected a scrape" becomes a real complaint. Enabling is refused, with a message in the row, for any copy whose path will not survive to the next login: a dev build, a copy still running from the mounted `.dmg`, and a Gatekeeper-translocated copy (this build is unsigned, so a quarantined first run executes from a random temporary mount). Remaining ceiling: macOS 13+ can disable the agent under System Settings → Login Items without touching the plist, and that switch is invisible to the toggle — reading it needs `SMAppService`.
- Signing/notarization + auto-update when distribution flips on (deferred by decision; structure — config file, CI matrix builds — ready from Phase 2). **STRUCTURE SHIPPED (inert).** `tauri.conf` macOS block (unset `signingIdentity` = no codesign unless `APPLE_SIGNING_IDENTITY` is present), `entitlements.plist` for the frozen-sidecar + downloaded-Chromium model, a tag/dispatch-only unsigned release CI matrix, and `docs/DISTRIBUTION.md`. Auto-update (`tauri-plugin-updater`) intentionally NOT added — it risked the cargo-1.78/edition2024 floor, which has since moved (rust-toolchain.toml pins 1.97.1, D2 2026-08-12); the plugin is now viable and waits only for distribution to flip on. Documented in DISTRIBUTION.md. Sidecar-into-bundle wiring is a documented TODO.
- Generate `THIRD-PARTY-NOTICES.md` from the lockfile (scrapling BSD-3, playwright Apache-2.0, patchright, curl_cffi, lxml, browserforge, Chromium) and show it in the about panel. **SHIPPED.** `engine/packaging/gen_notices.py` reads real installed license metadata (37 pkgs + a curated Chromium runtime-download note), and the About panel embeds the file via `include_str!`; a direct-dep test guards against a new lib shipping un-noticed.

## Backlog (explicitly deferred, not dropped)

| Item | Note |
|------|------|
| **Resilient-fetch tier** — backlogged fetch-reliability work | **SHIPPED (PR #12, single-proxy MVP).** `IOFAST_PROXY` routes every fetch tier (static/dynamic/stealth) through one proxy for IP-blocking sites; keychain-stored, request-scoped via the scrape `env` allowlist. Fast-follow: proxy-pool rotation (browser tiers need manual rotation + a list UI). |
| Auth-walled docs (cookie/session import from user's browser) | Pairs with CAPTCHA handoff work. Confluence is NOT here — its Phase 1 adapter uses the REST API with a user token |
| Corpus post-processing: chunking, embeddings, local semantic search | Covered by docs-mcp-server's splitter + hybrid search (2-REPO-ANALYSIS.md); own build only if that integration proves insufficient |
| Non-docs targets (blogs, wikis) | Out of scope until the 10-platform matrix is green |

## Decision log (interview, 2026-08-11)

| Decision | Choice |
|----------|--------|
| Audience | Personal first, structured for later distribution |
| Stack | Tauri + Python (Scrapling) sidecar |
| Crawl brain | 3-tier: adapters → heuristics → cached LLM rescue |
| Trigger UX | Menubar/tray + hotkey quick panel |
| Depth model | URL path-segment depth relative to the scope prefix (e.g. `/docs/api/v2/auth` = depth 3 under `/docs/`). Deterministic, computable from a sitemap with no crawl, identical for sitemap- and nav-derived maps. Link-hop depth dropped |
| Browser delivery | Downloaded on first use of the dynamic tier, NEVER bundled (PyInstaller cannot bundle Playwright browsers; keeps browsers outside the code signature) |
| Corpus trust | Scraped docs are untrusted, attacker-controllable input: wrapped in delimited blocks, `README-UNTRUSTED.md` per run, no auto-approve agent flags, argv-array execution |
| Phase 0 | Targeted ~3–4-session fixes to current scripts |
| Failure classes seen | JS-heavy, big-site explosion, bot-blocked |
| Success bar | 9/10 platform matrix, zero babysitting, run report |
| Library | App-managed, user-chosen root, Spotlight/Alfred indexable |
| Providers | BYO keys, OpenRouter-first; CLI agents auto-detected |
| Bot policy | Stealth + CAPTCHA handoff now; resilient-fetch backlogged **with nudge** |
