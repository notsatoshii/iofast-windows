# iofast — Analysis of Current Setup and Upstream Scrapling

Date: 2026-08-11. Sources: line-by-line read of `~/code/scrapling/` (run-scrapling.sh, doc_to_md.py, gitbook_to_md.py, README.md); upstream capability check of https://github.com/D4Vinci/Scrapling (fetched 2026-08-11).

## 1. Current setup — what exists

Flow today: press ctrl+opt+cmd+S → `run-scrapling.sh` reads clipboard URL → AppleScript dialog asks crawl depth (0–10, default 3) → `doc_to_md.py` (Scrapling Spider, dynamic/headless fetch) crawls same-domain links to that depth → each page's `<main>`/`<article>` converted to markdown via markdownify → files land in `docs-{domain}/` → wrapper moves the folder to `~/Documents/scraped/` with timestamp, zips it, opens Finder, plays a sound.

It works on simple, server-rendered docs sites. It fails intermittently elsewhere. The user-reported failure classes: JS-heavy sites (empty/partial pages), big sites (depth explosion, junk pages, long runs), bot-fronted sites (403/challenge, crawl dies).

## 2. Findings — script-level defects and gaps

### doc_to_md.py (universal crawler)

| # | Finding | Impact |
|---|---------|--------|
| 1 | `fetcher_type='dynamic'` for **every** page — full headless browser render even for static HTML | 5–20× slower than needed; big sites "hang" |
| 2 | Depth tracked via `response.meta.get('depth', 0)` set manually on follow — no guarantee the framework preserves it on redirects/dedup | Depth semantics unreliable; part of "sometimes it works" |
| 3 | Depth = raw link hops with **no path scoping** — follows any same-domain link (blog, pricing, changelog, locale variants) | Junk output, explosion on big sites |
| 4 | No sitemap.xml / llms.txt / llms-full.txt fast path — pure link discovery | Misses pages nav doesn't reach; wastes requests discovering what the site already lists |
| 5 | Content fallback chain ends at `response.body` — when `<main>`/`<article>` missing, whole page incl. header/footer/nav becomes "content" | Garbage markdown on non-standard layouts |
| 6 | markdownify gets raw fragment — no stripping of in-content nav, "Edit this page", breadcrumbs, cookie banners, script/style remnants | Noisy corpus, wasted LLM tokens downstream |
| 7 | Asset links (`.png`, `.zip`, `.pdf`) not filtered before follow | Wasted fetches; occasional binary-as-page writes |
| 8 | No retry, no per-page failure record, no end-of-run report | Silent partial scrapes — the worst failure mode (looks done, isn't) |
| 9 | Query-string stripping (`split('?')`) breaks sites where `?page=` is meaningful; anchor stripping fine | Rare missed pages |
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

## 3. Upstream Scrapling — what v0.4+ already ships (don't rebuild)

Verified against the GitHub repo 2026-08-11; local install is scrapling 0.4.2:

- **Three fetch tiers**: `Fetcher` (fast HTTP, TLS-fingerprint impersonation, HTTP/3), `DynamicFetcher` (Playwright browser), `StealthyFetcher` (fingerprint spoofing, Cloudflare Turnstile bypass) — exactly the static-first → dynamic → stealth escalation the engine needs; current scripts use only the middle, slowest-for-static tier.
- **SitemapSpider template** — sitemap-driven crawling out of the box (finding #4 largely solved upstream).
- **AutoThrottle** + per-domain throttling + robots.txt compliance with caching.
- **Pause/resume via checkpoints** — long crawls survive interruption.
- **Sessions** (sync/async), browser pool with `max_pages`, per-request proxy overrides, built-in ProxyRotator (feeds the backlogged max-evasion feature).
- **Built-in MCP server** + agent skill — candidate to reuse/wrap for iofast's MCP ingestion mode instead of writing one (fitness for serving a local library: unverified, flagged for Phase 1 reading).
- Adaptive element tracking (`find_similar`, similarity relocation) — useful when a platform adapter's selectors drift.

## 4. Gap between "have" and "want"

The current scripts use Scrapling ~10% — one fetcher type, no templates, no throttle, no checkpoints, no stealth. Most of Phase 0 is *deleting* homegrown logic in favor of upstream features, plus adding the deterministic intelligence upstream can't know: platform fingerprints, docs-path scoping, content cleanup, and run reporting.
