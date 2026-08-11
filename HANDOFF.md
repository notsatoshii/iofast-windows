# iofast — Session Handoff

Date: 2026-08-11. Repo: https://github.com/inputoutput9/iofast (private). Local: `/Users/james/code/iofast`. Head: `414ec32` on `main`.

## State

Planning is complete. No product code exists yet. Three docs are committed and pushed:

- `PLAN.md` — phased roadmap, decision log, backlog.
- `ANALYSIS.md` — current-script defects and installed-scrapling capability audit.
- `HANDOFF.md` — this file.

The plan passed a staff review and an independent verification pass. Both read the installed scrapling 0.4.2 source, not the README. The review found the first draft's diagnosis was wrong. The docs are now corrected.

## What the corrections changed (load-bearing)

1. The current script fetches static HTML only. `fetcher_type='dynamic'` is a dead attribute. That is why JavaScript docs come back empty. The fix is a new fetch-escalation ladder, not a speed tweak.
2. Scrapling 0.4.2 has no `SitemapSpider`, no AutoThrottle, and no robots.txt handling. The plan treats sitemap parsing, throttling, and robots as net-new code.
3. Playwright browsers add ~550MB and cannot be bundled by PyInstaller. Locked decision: download browsers on first use of the dynamic tier, never bundle them.
4. Scraped docs are untrusted input fed to agentic tools. Locked decision: wrap every scraped body as untrusted; never launch an agent CLI with auto-approve flags.
5. Phase 0 re-estimated from ~1 session to ~3–4 sessions.

## Next action

Start Phase 0. Target `~/code/scrapling/doc_to_md.py` and `run-scrapling.sh`. Work the 8-step table in `PLAN.md` Phase 0, in order. Verify against `docs.xmarket.app` plus one JavaScript-heavy site plus one large site.

## Standing nudge

The user asked to be reminded to build the max-evasion tier (proxy rotation, solver services) after the stealth-fetch tier ships. Raise it at the end of Phase 1 and Phase 4.

## Open assumptions to confirm during Phase 1

- The own-built MCP server needs `search_library` and `read_doc` tools over the local library. `mcp>=1.26.0` ships via scrapling's `ai` extra.
- The thin-content threshold that triggers tier escalation needs calibration against real pages before it is trusted.
- The "clean" numeric definition (≥95% in-scope URLs produce a file; zero files under N chars; link-to-text ratio cap) needs its N and cap set from measured fixtures.
