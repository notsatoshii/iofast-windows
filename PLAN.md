# iofast — Plan

"inputoutput fast": copy a docs URL → hotkey → intelligently scrape exactly the docs → ingest into the LLM tool of your choice. Deterministic first; LLM only when deterministic options are exhausted.

Decisions below were locked in an interview on 2026-08-11 (see Decision Log). Companion doc: [ANALYSIS.md](ANALYSIS.md).

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
│  Tier 2: heuristics (sitemap.xml, llms.txt, nav parse, readability)      │
│  Tier 3: LLM selector rescue (one call per domain, cached)               │
│  Fetch escalation: Fetcher → DynamicFetcher → StealthyFetcher            │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ plain markdown on disk (Spotlight/Alfred indexable)
┌───────────────▼── Library (user-chosen root, default ~/iofast) ──────────┐
│  <site>/<run-timestamp>/*.md + manifest.json + report.md                 │
│  Ingest: open-in-agent (claude/codex/cursor/gemini) · MCP · API push     │
└──────────────────────────────────────────────────────────────────────────┘
```

## Phase 0 — fix the current scripts (~1 session, nothing throwaway)

Target: `~/code/scrapling/doc_to_md.py` + wrapper. Every fix migrates verbatim into the sidecar engine later. Maps to ANALYSIS.md findings (F#).

| Step | Fix | Finding |
|------|-----|---------|
| 1 | Sitemap/llms.txt fast path: try `sitemap.xml`, `llms.txt`, `llms-full.txt` first (Scrapling SitemapSpider); fall back to link crawl only if absent | F4 |
| 2 | Docs-path scoping: derive prefix from start URL (e.g. `/docs/`); depth counts only within prefix; skip locale duplicates | F3 |
| 3 | Static-first fetch: `Fetcher` per page, escalate to `DynamicFetcher` only when content extraction comes back empty/thin; `StealthyFetcher` on 403/challenge | F1 |
| 4 | Content cleanup: readability-style extraction fallback instead of `response.body`; strip nav/breadcrumb/edit-links/cookie remnants pre-markdownify | F5, F6 |
| 5 | Link filtering: drop asset extensions, mailto/tel/js, offsite before enqueue; dedup before fetch | F7, F11 |
| 6 | Retry (2×, backoff) + per-page outcome ledger → `report.md` (pages ok / thin / failed, with reasons) — kills silent partial scrapes | F8 |
| 7 | Wrapper hygiene: trim clipboard, check python exit code, unique run dir instead of `rm -rf docs-*` + `ls -td` race | F14–16 |
| 8 | Delete `gitbook_to_md.py` (collisions, superseded) — universal + adapters replaces it | F12–13 |

Verify: run against docs.xmarket.app (known-good baseline) + one JS-heavy + one large site; pass = clean tree, no junk files, report lists zero silent failures, faster than current run on the same site.

## Phase 1 — engine as a product (CLI + adapters)

- Extract Phase 0 engine into `iofast-engine` Python package in this repo; CLI: `iofast scrape <url> [--scope … --depth … --json]`. The desktop app is a client of this; so is the old hotkey script (interim).
- **Platform adapters** with deterministic fingerprint detection (meta generator tags, asset paths, DOM markers), one module each: GitBook, Mintlify, Docusaurus, ReadMe, Redocly, Document360, Notion, Confluence, Read the Docs (Sphinx/MkDocs), SwaggerHub. Adapter = `{detect(), map_site(), content_selector, nav_selector, fetch_strategy}`.
- **Map-then-scrape**: map pass builds the section tree (sitemap/nav) → section picker + scoped depth → scrape pass. This is the depth-semantics decision.
- **Tier 3 rescue**: when adapters + heuristics fail, one LLM call (BYO key, OpenRouter-first) gets content/nav selectors from a sample page; result cached per-domain in `adapters-cache.json`; every rescue logged as a candidate permanent adapter.
- Run manifest per scrape: url, platform, scope, page ledger, token count of corpus.
- Test matrix in CI: one real site per platform, snapshot-based. **Success bar: 9/10 clean with zero manual intervention, ~2 min typical site, failure report always produced.**

## Phase 2 — desktop app MVP (Tauri + sidecar)

- Tauri 2.x shell, macOS + Windows 10/11. Python engine bundled via PyInstaller as sidecar; JSON-RPC over stdio; streamed progress events.
- Tray/menubar residence; global hotkey (Tauri global-shortcut) opens quick panel: clipboard URL pre-filled, live platform detection badge, page-count estimate from map pass, section/depth picker, ingest target dropdown. Enter = run.
- First-run: choose library root (remembered; changeable in settings). Files stay plain hyphen-named markdown — Spotlight/Alfred indexable is a hard requirement (no opaque DB as source of truth; index/metadata live alongside as JSON).
- Main window: library browser, run history, re-scrape/update button, live progress, settings (providers, keys in OS keychain, ingest defaults, hotkey).

## Phase 3 — ingestion modes

First-run chooser; remembered; changeable in settings (locked decision).

1. **Open in coding agent**: detect installed CLIs (`claude`, `codex`, `cursor`, `gemini`); one click opens the chosen tool with the scrape folder as working context. No keys needed.
2. **MCP server**: serve the library (search + read tools) to any MCP client. First evaluate reusing/wrapping Scrapling's built-in MCP server (flagged unverified — read it before deciding build-vs-wrap).
3. **API push**: send corpus + prompt to chosen provider (OpenRouter gateway default; native Anthropic/OpenAI/Gemini/DeepSeek slots), digest rendered in-app. Keys in OS keychain, never plaintext.

## Phase 4 — hardening + distribution

- CAPTCHA handoff: on unsolvable challenge, open visible browser window, user solves once, session continues.
- Pause/resume long crawls (Scrapling checkpoints), scheduled re-scrapes ("keep React docs fresh weekly").
- Signing/notarization + auto-update when distribution flips on (deferred by decision; structure — config file, CI matrix builds — ready from Phase 2).

## Backlog (explicitly deferred, not dropped)

| Item | Note |
|------|------|
| **Max evasion tier** — proxy rotation (Scrapling ProxyRotator), residential proxies, solver services | **NUDGE REQUIRED: user asked to be actively reminded to build this after stealth-tier ships.** Revisit at end of Phase 1 and Phase 4. |
| Auth-walled docs (cookie/session import from user's browser) | Pairs with CAPTCHA handoff work |
| Corpus post-processing: chunking, embeddings, local semantic search | Only after MCP mode proves insufficient |
| Non-docs targets (blogs, wikis) | Out of scope until the 10-platform matrix is green |

## Decision log (interview, 2026-08-11)

| Decision | Choice |
|----------|--------|
| Audience | Personal first, structured for later distribution |
| Stack | Tauri + Python (Scrapling) sidecar |
| Crawl brain | 3-tier: adapters → heuristics → cached LLM rescue |
| Trigger UX | Menubar/tray + hotkey quick panel |
| Depth model | Map-then-scrape; sections + scoped depth |
| Phase 0 | Targeted ~1-session fixes to current scripts |
| Failure classes seen | JS-heavy, big-site explosion, bot-blocked |
| Success bar | 9/10 platform matrix, zero babysitting, run report |
| Library | App-managed, user-chosen root, Spotlight/Alfred indexable |
| Providers | BYO keys, OpenRouter-first; CLI agents auto-detected |
| Bot policy | Stealth + CAPTCHA handoff now; max evasion backlogged **with nudge** |
