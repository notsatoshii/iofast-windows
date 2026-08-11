# iofast — Session Handoff

Date: 2026-08-11. Repo: https://github.com/inputoutput9/iofast (private). Phase 1 work sits on branch `jarvis/iofast-phase-1-resume-6081a8` (worktree `.claude/worktrees/iofast-phase-1-resume-6081a8`); see the PR for merge state. Engine client repo: https://github.com/inputoutput9/iofast-scraper, main @ `e7985cb`, pushed.

## State: Phase 1 COMPLETE — all 8 steps landed, DONE bar verified

The engine now lives in this repo as an installable package: `engine/` (dist `iofast-engine`, import `iofast_engine`, CLI `iofast`). 171 offline tests green (`engine/.venv/bin/pytest engine/tests -q`); CI workflow at `.github/workflows/engine-ci.yml` gates merges on them.

### DONE-bar verification (live, 2026-08-11)

Live matrix, 10 pages per platform, `iofast scrape <url> --max-pages 10`, zero manual intervention:

| Platform | Result |
|---|---|
| gitbook, mintlify, docusaurus, readme, redocly, readthedocs, confluence | 10 ok, clean=True, reconciled |
| document360 | 3 ok + 7 thin_escalated_ok (dynamic tier working), clean=True |
| swaggerhub | 7 ok via OpenAPI-spec rendering (never scrapes the SPA), clean=True |
| notion | documented known-fail: exit 1, report.md explains, ledger reconciles |

9/10 clean; Notion is the designed 10th. `gitbook_to_md.py` and `doc_to_md.py` are deleted from iofast-scraper (`e7985cb`), whose `run-scrapling.sh` is now a client calling `$IOFAST_BIN scrape` (default `~/code/iofast/engine/.venv/bin/iofast`).

### Engine architecture (engine/src/iofast_engine/)

- Pure modules (import no scrapling/playwright — fixture CI runs offline): `urls`, `ledger`, `extract`, `writer`, `manifest`, `clean`, `mapping`, `adapters/*`; `discovery` lazy-imports the fetcher. Only `spider.py` + `cli.py` touch scrapling. Enforced by `test_imports.py`.
- Events: no module below `cli.py` prints; everything emits `{"type": ...}` events. `--json` = NDJSON on stdout, terminal `result` event carries the manifest (JSON-RPC precursor for Phase 2). Human output goes to stderr.
- Exit codes: 0 ok · 1 completed-with-failures · 2 bad args · 3 nothing in scope.
- Adapters: frozen dataclass instances (subclassing forbidden, test-enforced), fingerprints cite the fixture file they came from. 7 HTML + SwaggerHub/Confluence (`fetch_mode="api"`, harvest is ledger-contracted) + Notion known-fail. Detection: one static GET of the resolved start URL; re-detects per page if the sample missed.
- `iofast map <url>`: discovery-only section tree (sitemap → llms.txt → nav parse). Scrape manifests carry `token_estimate` (chars/4) and a `clean` verdict block.
- Tier 3 rescue (`rescue.py`): armed by `OPENROUTER_API_KEY` + `IOFAST_RESCUE_MODEL` (both env, no hardcoded model id), one call per domain, cached in `~/.iofast/adapters-cache.json`, logged in the manifest as a candidate adapter.

### Measured numbers (recorded in code comments, from the committed fixtures)

- `THIN_CHARS = 250`: shell band reaches 218 (document360 static), real pages go down to 132 — bands overlap, so the trigger biases up; best-tier memory makes false escalations lossless.
- Clean = coverage ≥ 0.95 · zero files under 100 chars (junk ≤ 89, smallest real 132) · corpus-wide link-to-text ratio ≤ 0.50 (content pages 0.04–0.37; TOC pages hit 0.88, hence corpus-level).

### Fixed this session beyond the plan

- Cross-host redirects: final path wins when non-root (docs.readthedocs.io/en/stable/ → docs.readthedocs.com/platform/en/stable/; docs.gitbook.com → gitbook.com/docs/); user path restored only on a redirect-to-root.
- Scope-root page (`/docs` with scope `/docs/`) is depth 0 — it used to be silently excluded.
- Unparseable llms.txt URLs (gitbook's `https://[organization-name]...` placeholder) excluded instead of crashing the run.
- Exhausted 429/503 now report `http NNN (retries exhausted)` with real status.
- `-cn` locale twins drop only when the un-suffixed sibling exists (twin-based, not blanket).

## Known gaps / next session

1. **Phase 2 (PLAN.md)**: Tauri shell + PyInstaller sidecar. The NDJSON event stream and manifest schema (v1) are the sidecar contract — do not change shapes without bumping `schema`.
2. On a static-only run a real-but-tiny page (< 250 chars) still ships as `ok`; the clean verdict's 100-char floor is the backstop. Revisit if a real page under 100 chars ever appears.
3. RTD adapter's `nav_selector` (`nav.wy-nav-side`) is sphinx-rtd-theme-specific; python.org's custom theme falls back to generic `<nav>` (map quality only, scrape unaffected).
4. Scheduled live smoke run (PLAN's CI tier b, report-only) not set up yet — fixtures gate merges, nothing watches for live drift.
5. docs-mcp-server `file://` ingest (Phase 3 serve layer) still unvalidated end-to-end.
6. Primary-checkout setup after merge: `cd ~/code/iofast/engine && /opt/homebrew/bin/python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"` — the hotkey wrapper's default `IOFAST_BIN` expects this path.

## Standing nudge

Build the max-evasion tier (proxy rotation, solver services) — trigger armed since the stealth tier shipped in Phase 0. **This is the end-of-Phase-1 reminder: raise it with the user now**, and again at end of Phase 4.
