# iofast — Analysis of Current Setup and Upstream Scrapling

Date: 2026-08-11. Sources: line-by-line read of `~/code/iofast-scraper/` (run-scrapling.sh, doc_to_md.py, gitbook_to_md.py, README.md); upstream capability check of https://github.com/D4Vinci/Scrapling (fetched 2026-08-11).

## 1. Current setup — what exists

Flow today: press ctrl+opt+cmd+S → `run-scrapling.sh` reads clipboard URL → AppleScript dialog asks crawl depth (0–10, default 3) → `doc_to_md.py` (Scrapling Spider, **static HTTP fetch**) crawls same-domain links to that depth → each page's `<main>`/`<article>` converted to markdown via markdownify → files land in `docs-{domain}/` → wrapper moves the folder to `~/Documents/scraped/` with timestamp, zips it, opens Finder, plays a sound.

The script does NOT render JavaScript. `doc_to_md.py:16` sets `self.fetcher_type='dynamic'`, but `Spider` has no functional `fetcher_type`. Nothing reads that attribute. The script never overrides `configure_sessions()`. So the only fetch tier is `FetcherSession` — static HTTP. The dialog and the wrapper suggest a browser render that never happens.

It works on simple, server-rendered docs sites. It fails intermittently elsewhere. The user-reported failure classes: JS-heavy sites (empty/partial pages), big sites (depth explosion, junk pages, long runs), bot-fronted sites (403/challenge, crawl dies).

## 2. Findings — script-level defects and gaps

### doc_to_md.py (universal crawler)

| # | Finding | Impact |
|---|---------|--------|
| 1 | Static-only fetch. The script never renders JavaScript, so JS-rendered docs return an empty shell | JS-rendered pages come back empty or partial. Fix = ADD a dynamic/stealth escalation ladder (net-new), not make dynamic conditional |
| 2 | *Withdrawn — false.* `Response.follow` merges `meta` forward (engines/toolbelt/custom.py:130), so depth IS preserved. The real depth problem is that it is link-hop and unscoped — see F3 | — |
| 3 | Depth = raw link hops with **no path scoping** — follows any same-domain link (blog, pricing, changelog, locale variants) | Junk output, explosion on big sites |
| 4 | No sitemap.xml / llms.txt / llms-full.txt fast path — pure link discovery | Misses pages nav doesn't reach; wastes requests discovering what the site already lists |
| 5 | Content fallback chain ends at `response.body` — when `<main>`/`<article>` missing, whole page incl. header/footer/nav becomes "content" | Garbage markdown on non-standard layouts |
| 6 | markdownify gets raw fragment — no stripping of in-content nav, "Edit this page", breadcrumbs, cookie banners, script/style remnants | Noisy corpus, wasted LLM tokens downstream |
| 7 | Asset links (`.png`, `.zip`, `.pdf`) not filtered before follow | Wasted fetches; occasional binary-as-page writes |
| 8 | No retry, no per-page failure record, no end-of-run report | Silent partial scrapes — the worst failure mode (looks done, isn't) |
| 9 | Query-string stripping (`split('?')`) is currently the ONLY guard against infinite crawl of `?ref=`/`?utm_`/faceted/session-param URLs | Keep stripping. Do NOT stop to catch `?page=`. Strip all params by default; keep a per-adapter allowlist of meaningful params |
| 10 | Nav/TOC detection is selector roulette (5 hardcoded guesses) and produces a **text-art tree only** — never used to plan the crawl | The one intelligence pass is decorative |
| 11 | `visited_clean_urls` dedup happens **after** fetch, not before enqueue | Duplicate fetch cost |

### gitbook_to_md.py (GitBook crawler)

| # | Finding | Impact |
|---|---------|--------|
| 12 | Filenames use last path segment only — `/a/setup` and `/b/setup` collide, second overwrites first | Data loss |
| 13 | Superset of doc_to_md.py defects (older copy); underscore naming inconsistent with hyphen convention | Redundant file; should be deleted, universal + a GitBook adapter replaces it |

### run-scrapling.sh (wrapper)

| # | Finding | Impact |
|---|---------|--------|
| 14 | `rm -rf docs-*` cleanup then `ls -td docs-* \| head -1` to find output — racy, and destroys any previous unmoved run | Fragile handoff; potential silent data loss |
| 15 | No trim/whitespace normalization of clipboard; URL with trailing newline can pass the regex | Occasional bogus failures |
| 16 | Exit code of python not checked — "success" path runs even if spider crashed after making the folder | False green |
| 17 | macOS-only by construction (pbpaste/osascript/afplay) | Fine for today; the desktop app replaces it |

### What's genuinely good (keep)

- Clipboard-hotkey ergonomics — the core UX insight; iofast keeps it as the primary trigger.
- Collision-proof full-path filenames in doc_to_md.py; `--- SOURCE: url ---` provenance header per file.
- Hyphenated naming for Spotlight/Alfred indexability — carries into the iofast library as a hard requirement.
- Same-domain fencing via `allowed_domains`.

## 3. Upstream Scrapling — what v0.4.2 actually ships

Verified by running grep/python against the installed package at `~/code/iofast-scraper/.venv/lib/python3.13/site-packages/scrapling/` (scrapling 0.4.2).

**Real and reusable:**

- **Three fetch tiers**: `Fetcher` (fast HTTP, TLS-fingerprint impersonation, HTTP/3), `DynamicFetcher` (Playwright browser), `StealthyFetcher` (fingerprint spoofing, Cloudflare Turnstile bypass). This is exactly the static → dynamic → stealth escalation the engine needs. The Turnstile bypass in `StealthyFetcher` is real.
- **The escalation MECHANISM already exists.** `SessionManager` (session.py) holds `FetcherSession` + `AsyncDynamicSession` + `AsyncStealthySession` together. A per-request `sid` selects the tier. We wire the ladder; we do not build the plumbing.
- **Checkpoints are real** — `spiders/checkpoint.py`, `CheckpointManager`. Long crawls survive interruption via pause/resume.
- **Sessions** (sync/async), browser pool with `max_pages` (capabilities that feed the backlogged resilient-fetch tier).
- Adaptive element tracking (`find_similar`, similarity relocation) — useful when a platform adapter's selectors drift.

**Claimed before but NOT in 0.4.2 — we must write these:**

- **No `SitemapSpider`.** `scrapling.spiders.__all__` = `Spider, SessionConfigurationError, Request, CrawlerEngine, CrawlResult, SessionManager, Scheduler, Response`. Sitemap and llms.txt handling is net-new code.
- **No AutoThrottle and no robots.txt handling.** The only rate controls are static `Spider` attributes: `concurrent_requests=4`, `concurrent_requests_per_domain=0` (unlimited), `download_delay=0.0`. The default politeness is unsafe for unattended use. We must set `download_delay` and a per-domain cap ourselves, and write robots.txt handling with stdlib `urllib.robotparser`. Record our robots policy as a deliberate decision.

**Retry — partial, not absent.** Scrapling DOES retry on a fixed blocked-code set (401, 403, 407, 429, 444, 500, 502, 503, 504) up to `max_blocked_retries=3`. It does NOT retry on a raised exception — `engine.py` catches it, calls `on_error`, and drops the request. So our Phase 0 retry work = add retry-on-exception, plus build the per-page outcome ledger.

**MCP — build our own, do not wrap the built-in.** Scrapling's built-in server (`core/ai.py`) exposes only live-fetch tools: `get`, `bulk_get`, `fetch`, `bulk_fetch`, `stealthy_fetch`, `bulk_stealthy_fetch` — all take URLs. It has NO local-corpus search and no read-file tool. It is a fetch-the-web server, not a serve-my-library server. We build our own MCP server; we do not wrap this one.

## 3a. Licensing

Scrapling is **BSD 3-Clause** (verified from dist-info METADATA). Clause 3 requires reproducing the notice in binary redistributions. The transitive set — playwright (Apache-2.0), patchright, curl_cffi, lxml, browserforge, and Chromium if ever bundled — needs a `THIRD-PARTY-NOTICES` file at distribution time.

## 4. Gap between "have" and "want"

The current scripts use one fetch tier, no checkpoints, no stealth. Phase 0 is mostly *net-new* code — sitemap/llms.txt parsing, an escalation ladder, robots handling, and a per-page ledger — because upstream ships none of those. It also adds the deterministic intelligence upstream can't know: platform fingerprints, docs-path scoping, content cleanup, and run reporting. Upstream contributes the fetch tiers, the `SessionManager` plumbing, and checkpoints.

Note what carries into the sidecar. The file-writing, folder-naming, and print-progress code does NOT carry verbatim into a JSON-RPC-streaming sidecar — that I/O layer gets rewritten. What truly carries: path-scoping, content cleanup, link filtering, and the ledger logic.
