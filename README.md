# iofast

A macOS desktop app that copies a documentation site into a folder of clean
markdown on your own disk, so coding agents and search tools can read it
offline. Point it at a docs URL, pick how much of the site you want, and it
writes one hyphen-named `.md` file per page plus a report of exactly what
succeeded and what did not.

Built for one person's own machine first (see [Status](#status-and-limits)).
It is a Tauri shell (Rust + React) driving a Python crawler that runs as a
separate helper process, talking over a line-based protocol on standard input
and output (JSON-RPC over stdio).

```
tray icon + hotkey panel  ->  Tauri shell (Rust/React)
                                  |  JSON-RPC over stdio
                              iofast_engine (Python, frozen with PyInstaller)
                                  |  plain markdown on disk
                              library root  ->  open in an agent | MCP search | API push
```

## Docs

| Document | What it is for |
|---|---|
| [PLAN.md](PLAN.md) | The build plan: phases, what shipped, and the decision log behind each choice. Start here. |
| [docs/RPC.md](docs/RPC.md) | The exact contract between the app and the engine — every method, parameter, event, and error code. |
| [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) | How builds are packaged today (unsigned) and what it would take to turn on signing, notarization, and auto-update. |
| [ANALYSIS.md](ANALYSIS.md) | Line-by-line read of the original hotkey scripts and of upstream Scrapling — the findings this project was written to fix. |
| [2-REPO-ANALYSIS.md](2-REPO-ANALYSIS.md) | Prior-art review of docs-mcp-server and Scrapegraph-ai, and the reasoning for reusing the first and rejecting the second. |
| [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) | Licenses of every package bundled in the frozen engine, generated from installed package metadata. |

## How it works

### Finding the pages

The crawl is deterministic first. A language model is only involved when the
deterministic paths have run out.

| Tier | What it does |
|---|---|
| 1. Platform adapters | Fingerprints the docs platform and uses selectors written for it. Modules exist for GitBook, Mintlify, Docusaurus, ReadMe, Redocly, Document360, Read the Docs, plus SwaggerHub and Confluence (which read the site's API instead of its HTML). Notion is recorded as a documented known-fail. |
| 2. Heuristics | `sitemap.xml` and `llms.txt` as the fast path, navigation parsing otherwise, and trafilatura to pull the article text out of a page. |
| 3. Selector rescue | When the first two fail, one model call per domain (your own OpenRouter key, `OPENROUTER_API_KEY` + `IOFAST_RESCUE_MODEL`) asks for the content and navigation selectors from a sample page. The answer is cached per domain in `adapters-cache.json` and logged as a candidate for a permanent adapter. |

Scope is a URL path prefix (`/docs/`), and depth counts path segments beyond
that prefix, so it is computable from a sitemap without crawling anything.
Politeness defaults are set in the crawler itself: half a second between
requests, four concurrent requests, two per domain, with `robots.txt` fetched
and applied.

Fetching escalates through three modes — `static`, `dynamic`, `stealth` —
where the second and third drive a real browser and are used when a static
fetch comes back near-empty or with a challenge page instead of content.
Setting `IOFAST_PROXY` routes every mode through one proxy, for sites that
limit requests by IP address.

Every page lands in a per-run ledger, so a run always ends with a
`report.md` listing pages that succeeded, came back thin, or failed, and why.
A run folder holds the markdown files plus `manifest.json`, `report.md`, and
`README-UNTRUSTED.md`. Stop pauses and checkpoints instead of killing the
process, so the ledger survives; a later run can resume that checkpoint, or
re-check an already-complete site with conditional requests (a page that has
not changed costs one small response and keeps its existing file).

### Using a corpus

Three ways, chosen on first run and changeable in settings:

1. **Open in a coding agent.** The app detects `claude`, `codex`, `gemini`,
   and `cursor` on your machine and opens the chosen one with the run folder
   as its working directory. No keys needed.
2. **MCP search index.** The app indexes a run into a shared store using
   `@arabold/docs-mcp-server` (unforked, run through `npx`), then hands you a
   read-only stdio config to paste into your MCP client. Files on disk stay
   the source of truth; the index is derived from them.
3. **API push.** The engine reads the run's markdown pages, concatenates them
   under a character cap, and sends them to OpenRouter with your prompt. The
   answer streams back into a card in the app. Keys live in the macOS keychain
   and are handed to the engine per request, never on a command line.

## Install and run (dev)

Requires macOS, Python 3.13 (3.14 is deliberately not admitted), Node with
pnpm, and a Rust toolchain.

**Engine.**

```bash
python3.13 -m venv engine/.venv
engine/.venv/bin/pip install -e "engine[dev,build]"
engine/.venv/bin/pytest engine/tests -q
```

The test suite is offline by design — committed HTML fixtures stand in for the
live web, so it installs no browsers and makes no network requests.

The engine is also usable on its own:

```bash
engine/.venv/bin/iofast map https://docs.example.com/docs/
engine/.venv/bin/iofast scrape https://docs.example.com/docs/ --out ./out
```

`iofast estimate` gives a fast page count, and `iofast rpc` starts the
stdio server the app talks to. `iofast combine <site-dir>` joins every
section of a hotkey site folder into one LLM-ready `<site-dir>/<site-dir>.md`:
pages in the site's own order, repeated boilerplate removed.

**Frozen engine (the sidecar the app runs).** Build it before starting the
app. In dev the shell looks for `engine/dist/iofast/iofast` (or wherever
`IOFAST_BIN` points — dev builds only); a release build bundles this tree into
the `.app` via `bundle.resources`, so it must exist before `pnpm tauri build`.

```bash
make freeze
```

`verify_frozen.py` (which `make freeze` runs) is the acceptance gate: it
drives the real frozen binary over stdio, including a scrape against a local
fixture server, and asserts the bundle's freeze-time git SHA matches the
tree. `make gate` runs the full pre-merge gate (pytest, cargo, frontend
build, frozen verify); `make bundle` is the only sanctioned way to produce
the `.app` — a bare `pnpm tauri build` packages whatever stale `engine/dist`
is on disk.

**App.**

```bash
cd app && pnpm install && pnpm tauri dev
```

`app/src-tauri/Cargo.lock` is load-bearing and committed. The toolchain pin
lives in `app/src-tauri/rust-toolchain.toml` (1.97.1); `Cargo.toml`'s
`rust-version` (1.77.2) is the MSRV floor, not the pin. A fresh
`cargo update` can pull in crates that need a newer compiler than the floor —
only re-resolve deliberately, on a toolchain bump, and update
`rust-toolchain.toml`, `Cargo.toml`'s `rust-version`, `release.yml`'s
toolchain step, and this paragraph together.

## Scraped content is untrusted

Scraped documentation is input someone else controls, and it is handed to
tools that act on text. The repo treats it that way throughout: every scraped
file opens with a `SOURCE:` line and an `UNTRUSTED CONTENT` banner, every run
folder gets a `README-UNTRUSTED.md` saying the folder is data and not
instructions, the API-push system prompt repeats it, agent CLIs are never
launched with auto-approve flags, and paths are passed as argument arrays
rather than spliced into shell strings. `report.md` and `manifest.json` are
written by iofast itself and are the only files in a run folder that are not
scraped.

## Status and limits

- **Personal-first.** Built for one machine. It is structured so distribution
  is possible later, but that is not the current target.
- **macOS-first.** The shell, the release workflow, and the folder-picker and
  terminal integrations are macOS-only today. Windows work (long-path limits,
  stdio in a windowed build, hotkey and notification parity) is planned but
  not done.
- **Builds are unsigned, and distribution is off.** `pnpm tauri build`
  produces an unsigned `.app` and `.dmg`; the release workflow builds the same
  thing on a tag with no secrets. It runs locally, but Gatekeeper will warn
  anyone else who downloads it. Signing and notarization are wired as
  structure only — see [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md).
  Auto-update is deliberately not added yet, because the plugin crate risks
  raising the toolchain floor.
- **The frozen engine fetches statically only.** Browsers are never bundled
  (PyInstaller cannot bundle them, and keeping them outside the app keeps
  notarization tractable), and the browser drivers are stripped from the
  bundle, so the dynamic and stealth modes are switched off in a frozen build.
  The on-demand browser download that would turn them back on is not built
  yet; the dynamic tier works when running the engine from a source checkout
  with Playwright's Chromium installed.
- **Handing off a challenge page to the user** to complete in a visible
  browser window is deferred, since it sits on that same browser tier.
- Auth-walled docs beyond Confluence, and non-documentation targets such as
  blogs and wikis, are out of scope for now.
