# iofast — Session Handoff

Date: 2026-08-11. Repo: https://github.com/inputoutput9/iofast (private). Local primary: `/Users/james/code/iofast` (main @ `33d7018`). This session's work sits on branch `jarvis/iofast-prior-art-gate-c14cd6` (worktree `.claude/worktrees/iofast-prior-art-gate-c14cd6`), NOT yet merged or pushed.

## State

Step 0 (prior-art gate) and Phase 0 are both COMPLETE and verified.

### Step 0 — prior-art gate (this repo, branch above)

- Cloned and source-analyzed `~/code/docs-mcp-server` (@ 586b5fa, v3.0.0) and `~/code/Scrapegraph-ai` (@ 27d9d28, v2.1.6).
- Verdict: **HARVEST** — full evidence in `2-REPO-ANALYSIS.md` (commit `c16dbd1`).
  - Scrapling crawl brain stays as planned (neither repo ships a deterministic crawl brain).
  - **docs-mcp-server adopted unforked as the default serve layer**: Phase 3 MCP mode now = point it at the library via `file://` ingest (MCP search + chunking + hybrid FTS/vector + versioned index + job queue + web UI, free; degrades to FTS-only without any API key). Own FastMCP server demoted to fallback.
  - Scrapegraph-ai rejected entirely: LLM mandatory in constructor, no crawl brain, deterministic crawl is their paid API, default telemetry POSTs scraped content off-box.
- PLAN.md updated in the same commit: Phase 3 mode 2 rewritten, backlog chunking row marked covered, Phase 0 steps 1–3 annotated with harvested tricks (llms.txt `.md`-variant probe, redirect scope-anchor rule, default excludes, Cloudflare challenge markers).

### Phase 0 — scripts fixed (separate repo: `~/code/scrapling`, now a git repo, branch main)

All 8 PLAN.md steps landed, one commit each (`45f16e3`..`6cb2b93`; baseline `b128bed`). `doc_to_md.py` now has: sitemap/llms.txt discovery, docs-path scoping with segment depth, static→dynamic→stealth escalation (monotonic, thin-content threshold 200 chars — measured, see below), trafilatura extraction (raw body never dumped), enqueue-time link filtering/dedup, retry-on-exception, per-page ledger → `report.md` + `manifest.json`, politeness (delay 0.5s, per-domain 2) + robots.txt obedience, `--selftest`, `--max-pages`, `--out`. Wrapper race (`rm -rf docs-*`) is gone; run dir is created up front and passed via `--out`.

Threshold 200 was measured: real-page extraction band starts ~224 chars (docs.xmarket.app sitemap sample, median 779); JS-shell band is 0–188 (uniswap 0, gitbook root 188, ant.design 42 static vs 4088 dynamic). Best-tier memory means a false escalation never loses content.

### Phase 0 verify — PASSED (independently spot-checked, not just agent-reported)

| Run | Site | ok | thin_escalated_ok | failed | Reconciles |
|---|---|---|---|---|---|
| a | docs.xmarket.app (baseline, sitemap) | 40 | 0 | 0 | 40/40 |
| b | ant.design/docs/react (JS-heavy, sitemap) | 0 | 40 | 0 | 40/40 |
| c | docs.python.org/3/library (large, link crawl) | 40 | 0 | 0 | 40/40 |

Each tree: exactly 40 `.md` + `report.md` + `manifest.json`, zero empty/junk files, smallest file 229 B (real content). Run b proves the ladder: every page escalated static→dynamic, none needed stealth, smallest output 535 B of real rendered content. Run outputs (ephemeral): `/private/tmp/verify-a`, `/private/tmp/verify-b`, `/private/tmp/verify-c2`.

## Known gaps (deliberate, from the build report)

- Locale twins caught as directories (`/fr/…`) only; suffix twins (`-cn`) pass through — run b has 20 Chinese duplicates. Belongs in the Phase 1 platform adapter, not a blanket suffix rule.
- Start URL without trailing slash scopes to its parent directory (matches docs-mcp-server's rule; user adds the slash).
- Exhausted transient codes (429/503 past upstream's 3 retries) surface as `failed_fetch` "no response recorded", without the status code.
- When `--max-pages` truncates, the report shows in-scope total next to enqueued count rather than balancing them.
- `doc_to_md.py` is 842 lines (target was ~450) — banner-separated; Phase 1 extracts it into a package anyway.

## Next actions

1. Merge branch `jarvis/iofast-prior-art-gate-c14cd6` → main and push (user decision; nothing pushed yet). `~/code/scrapling` is a fresh local-only git repo — decide whether it gets a remote.
2. Phase 1 (PLAN.md): extract engine into `iofast-engine` package in this repo; platform adapters (7 HTML + SwaggerHub/Confluence API + Notion known-fail); map-then-scrape; Tier 3 LLM rescue; committed HTML fixtures + MUST-FAIL ledger fixtures; delete `gitbook_to_md.py` once GitBook adapter passes.
3. Fold Phase 0's known gaps into Phase 1 adapter work (locale suffixes, status-code plumbing).

## Standing nudge

Remind the user to build the max-evasion tier (proxy rotation, solver services) at end of Phase 1 and Phase 4 — stealth tier now exists (Phase 0 step 3), so the trigger condition is armed.

## Open assumptions to confirm during Phase 1

- docs-mcp-server `file://` ingest as serve layer: validate end-to-end once a real library dir exists (its SQLite duplicates content; files stay source of truth; its MCP tool descriptions cannot carry the untrusted-corpus warning unforked — per-file wrappers must suffice).
- The 200-char threshold held for 3 sites; re-calibrate against the Phase 1 fixture matrix.
- "Clean" numeric definition (≥95% in-scope produce a file; zero under N chars; link-to-text cap) still needs N and cap set from fixtures.
