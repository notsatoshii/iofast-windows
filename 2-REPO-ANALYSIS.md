# iofast — Prior-Art Gate (Step 0): docs-mcp-server + Scrapegraph-ai

Date: 2026-08-11. Method: both repos cloned and read at source level (not README) by two independent analysis passes; verdict decided against PLAN.md's goals. Clones: `~/code/docs-mcp-server` @ `586b5fa` (v3.0.0, 2026-08-08), `~/code/Scrapegraph-ai` @ `27d9d28` (v2.1.6, 2026-07-20).

## Verdict: HARVEST

Keep the Scrapling-based deterministic crawl brain exactly as planned. Reuse **docs-mcp-server unforked** as the recommended MCP/search/serve layer over the iofast library (it ingests a `file://` markdown corpus with zero code changes), and lift a short list of specific techniques from it into Phases 0–1. **Reject Scrapegraph-ai as a base entirely**; nothing structural to harvest.

This captures the "avoid double work" benefit ADOPT was meant to buy — everything downstream of clean markdown (chunking, hybrid search, versioned store, MCP server, job queue, web UI) already exists, MIT-licensed and actively maintained — without forking a codebase whose storage model and crawl engine both contradict iofast's core requirements.

---

## Repo 1: arabold/docs-mcp-server (v3.0.0)

TypeScript/Node ≥22, MIT (`package.json:8,150`). 206 commits in last 6 months, single maintainer (186/209 commits), 1,634 stars, real CI + retrieval-quality eval harness (promptfoo, `tests/search-eval/`). Genuinely well-engineered.

### Coverage vs iofast's goal

| iofast need | docs-mcp-server has | Evidence |
|---|---|---|
| Multi-source doc scraping | Partial — web/file/npm/pypi/github strategies, URL-prefix dispatch only | `src/scraper/ScraperRegistry.ts:28-62` |
| Platform adapters by fingerprint | **No** — no detection; only MkDocs/Sphinx CSS selectors in sanitizer | `HtmlSanitizerMiddleware.ts:156-160` |
| Static→dynamic→stealth escalation | **No** — 2-trigger escalation only (Cloudflare `ChallengeError`, `TlsCertificateError` → plain Playwright); no thin-content trigger, no stealth tier at all | `AutoDetectFetcher.ts:62-74`, `BrowserFetcher.ts:317-323` |
| Sitemap.xml fast path | **No** — zero hits in `src/` | grep |
| llms.txt fast path | **Yes**, first-class, incl. a `.md`-variant probe trick | `WebScraperStrategy.ts:104-167,194-229` |
| robots.txt / rate limiting | **No** — neither exists; only batch concurrency 3 | `BaseScraperStrategy.ts:530-535`, `config.ts:105` |
| Docs-path scoping | Yes — subpages/hostname/domain scopes, psl-based | `src/scraper/utils/scope.ts:60-87` |
| Per-page outcome ledger | **Partial** — progress events per page, but failed pages never persisted; one `error_message` per version | `BaseScraperStrategy.ts:177-184`, `005-add-status-tracking.sql:9` |
| Version-aware indexing | **Yes** — library@version, semver resolution, ETag-based refresh, 404 pruning | `009-add-pages-table.sql`, `WebScraperStrategy.ts:343-351` |
| Chunking | **Yes, excellent** — heading-aware `SemanticMarkdownSplitter` + `GreedySplitter` ("structure > size") | `src/splitter/SemanticMarkdownSplitter.ts:39-105`, `GreedySplitter.ts:43-95` |
| Search | **Yes** — hybrid RRF (sqlite-vec KNN + FTS5 BM25), degrades cleanly to FTS-only with no API key | `DocumentStore.ts:2024-2178,632-651` |
| Job queue + web UI | **Yes** — in-process queue w/ restart recovery + React 19 SPA w/ live progress | `PipelineManager.ts:30-32,112-183`, `src/web/client/pages/*` |
| MCP serve | **Yes** — stdio + HTTP, 10 tools incl. `search_docs`; **no** `read_doc` (only live `fetch_url`) | `src/mcp/mcpServer.ts:42-473` |
| Plain markdown on disk | **NO — DB-locked.** Chunks live in SQLite only; no markdown ever written to disk | `DocumentManagementService.ts:459-486`; only `writeFile`s are temp archive/config/telemetry-id |
| Ingest a foreign markdown corpus | **Yes** — `LocalFileStrategy` indexes a `file://` directory tree | `LocalFileStrategy.ts:27-52`, `ScraperRegistry.ts:36-39` |

### Why not ADOPT (fork / build on top)

1. **Storage inversion.** Spotlight/Alfred-indexable plain markdown is a hard iofast requirement (PLAN.md Phase 2). docs-mcp-server's source of truth is SQLite; making it file-first is an architectural inversion of someone else's actively moving codebase, not a feature add.
2. **No seam for an external scraper.** Strategies are synchronous in-process TS classes (`src/scraper/types.ts:30-43`); no plugin registry, no subprocess/IPC contract. A Python/Scrapling sidecar has no place to attach without forking `ScraperRegistry`.
3. **The crawl engine is deliberately modest.** No sitemap, no robots, no rate limiting, no fingerprint adapters, no stealth. All of iofast Phases 0–1 would still be written — in TS, discarding Scrapling's verified assets (three fetch tiers, Turnstile bypass, SessionManager escalation, checkpoints; ANALYSIS.md §3).
4. **Fork treadmill.** Single maintainer shipping ~weekly (206 commits/6 mo). A fork that inverts storage diverges immediately and permanently.
5. **The tray/hotkey/Tauri UX layer** — iofast's trigger insight — doesn't exist here and would be new work under either verdict.

### Why not REJECT

The serve half is real and better than a from-scratch v1: chunking, RRF hybrid search with clean FTS-only degradation (no cloud key required — `DocumentStore.ts:642-651`), versioned store with conditional-GET refresh, MCP on stdio+HTTP, queue with restart recovery, working UI, and an eval harness. Rebuilding that to parity is weeks of work iofast doesn't need to spend. Unforked integration costs ~zero: `scrape mylib file:///path/to/iofast-library`.

### Harvest list (into iofast)

| What | From | Into |
|---|---|---|
| **Unforked serve layer**: point docs-mcp-server at the library root via `file://` ingest for MCP search + web UI + versioned index | `ScraperRegistry.ts:36-39`, `LocalFileStrategy.ts:27-52` | Phase 3 mode 2 (replaces "build our own MCP server" as the default; own FastMCP stays as the no-Node fallback) |
| llms.txt probe incl. trying the `.md`/`index.html.md` variant of each URL before HTML | `WebScraperStrategy.ts:104-167` | Phase 0 step 1 |
| Redirect scope-anchor rule: depth-0 redirect adopts protocol+host but keeps user path as scope anchor | `WebScraperStrategy.ts:273-291` | Phase 0 step 2 |
| Default exclude patterns (changelogs, licenses, i18n locales, tests) | `src/scraper/utils/defaultPatterns.ts:11-140` | Phase 0 step 2/5 |
| Cloudflare challenge detection markers (`cf-mitigated: challenge`, `Just a moment...`, `cf_chl_opt`) | `HttpFetcher.ts:300-330` | Phase 0 step 3 (escalation trigger) |
| ETag/If-None-Match conditional refresh + 304 skip + 404 prune | `HttpFetcher.ts:130-174`, `WebScraperStrategy.ts:343-351` | Phase 2 re-scrape (already planned; now with prior art) |
| Heading-aware two-phase chunking design (if we ever chunk ourselves) | `SemanticMarkdownSplitter.ts` + `GreedySplitter.ts` | Backlog (likely never needed — docs-mcp-server does it) |

Caveats of the unforked route: corpus content gets duplicated into its SQLite (files stay the upstream source of truth); its MCP tool descriptions can't carry iofast's untrusted-content warning without a fork — the per-file untrusted wrappers and `README-UNTRUSTED.md` (Phase 3 rules) still travel inside the markdown itself, which survives ingestion. No local/offline embedding provider — without a cloud key it runs FTS-only, which is acceptable.

---

## Repo 2: ScrapeGraphAI/Scrapegraph-ai (v2.1.6)

Python ≥3.12, MIT (`pyproject.toml:66`, `LICENSE`). LangChain-based; self-description: "uses LLM and direct graph logic" (`pyproject.toml:6`). Hosted-API SDK `scrapegraph-py>=2.0.0` is a hard dependency (`pyproject.toml:32`).

### Why REJECT as a base (evidence)

1. **LLM is mandatory by construction.** `AbstractGraph.__init__` does `self.llm_model = self._create_llm(config["llm"])` unguarded (`abstract_graph.py:67`); every exported graph inherits it. Deterministic-first is impossible without rewriting the class hierarchy.
2. **The deterministic site-crawl-to-markdown product is their PAID API, not the OSS package.** README's own comparison table: managed API gets crawl/stealth/monitor jobs; OSS gets "graph pipelines" (`README.md:172-188`). `SmartScraperGraph` short-circuits to `api.scrapegraphai.com` for model string `scrapegraphai/smart-scraper` (`smart_scraper_graph.py:79-95`).
3. **No crawl brain.** No sitemap/llms.txt anywhere (grep: zero hits). No path scoping — `only_inside_links` actually excludes same-domain *absolute* links, keeping only relative hrefs (`fetch_node_level_k.py:216`). No fetch escalation (one backend chosen at construction, `chromium.py:75-84`). `retry_limit=1` (`chromium.py:37`). No politeness. No per-page ledger. robots.txt compliance is adjudicated by an LLM parsing "yes/no" from free text (`robots_node.py:93-113`).
4. **Markdown output isn't the product.** Output is JSON answers to prompts; markdown conversion is gated on the LLM vendor being OpenAI/Azure (`fetch_node.py:388-398`); the `MarkdownifyGraph` is unexported dead code with a type mismatch (`markdownify_graph.py` vs `graphs/__init__.py:32-62`, `fetch_node.py:400-408` writes a Document list where `markdownify_node.py:59-62` expects a string).
5. **The static-fetch path is broken in the tagged release** — unbound variable when `use_soup=True` and `cut=True` (default), plus a 5-tuple passed where a string is expected (`fetch_node.py:294-302`, `cleanup_html.py:95`). CI excludes the fetch/crawl test suites entirely (`.github/workflows/test-suite.yml`), consistent with these surviving.
6. **Telemetry POSTs scraped page content off-box by default** — `{user_prompt, website_content, llm_response, url}` to `sgai-oss-tracing.onrender.com` in a daemon thread (`telemetry/telemetry.py:80-152`); the env-var opt-out reads the config file, not the env var (`:47-53`). Disqualifying default for a tool that scrapes private docs.
7. Cost shape wrong: 500-page docs site ≈ 500–1500+ LLM completions each carrying full page text (`generate_answer_node.py:180-256`). iofast's Tier 3 is one cached call per *domain*.

Harvestable at most as reference, not code: `undetected_playwright` stealth application (`chromium.py:361-372`) — redundant, Scrapling's StealthyFetcher already covers this; its 27-provider LLM config layer (`abstract_graph.py:155-282`) — OpenRouter-first plan makes this unnecessary. Nothing enters the plan.

---

## PLAN.md changes applied (same commit)

1. Phase 3 mode 2: docs-mcp-server (unforked, `file://` ingest) becomes the default MCP/search/serve path; own FastMCP server demoted to fallback (no-Node environments / if untrusted-marking inside tool descriptions becomes mandatory).
2. Backlog "corpus post-processing (chunking/embeddings/semantic search)" marked as covered by docs-mcp-server rather than future own-build.
3. Phase 0 steps 1–3 annotated with the specific harvested techniques (llms.txt `.md`-variant probe, redirect scope-anchor rule, default exclude patterns, Cloudflare challenge markers).

Phase 0 itself is unchanged: it is entirely crawl-side, and neither repo ships the crawl brain. Proceed to Phase 0 as planned.
