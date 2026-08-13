# iofast sidecar JSON-RPC contract (protocol 1)

The engine's second front end. `iofast rpc` starts a JSON-RPC 2.0 server over
stdio. The CLI (`iofast scrape` / `iofast map`) is unchanged; both front ends
call the same runner functions (`runner.py`). Phase 3 and 4 inherit this
contract.

## Framing

One JSON object per line (LF, UTF-8) in both directions — same framing the
`--json` NDJSON stream already uses. No Content-Length headers. A line that is
not valid JSON gets a `-32700` error response with `id: null`. Serialization
is `json.dumps(obj, default=str)` — identical to the CLI's NDJSON emitter.

## Process lifecycle

- The Tauri shell spawns `iofast rpc`, holds stdin/stdout pipes, and logs
  stderr (human noise, never protocol).
- **stdout hardening**: at startup the server dups fd 1 for protocol output
  and redirects fd 1 to stderr, so a dependency that prints can never corrupt
  the protocol stream. The invariant is structural, not advisory.
- **EOF on stdin = shutdown**: the emit sink is swapped to a no-op immediately
  (the parent is gone; writes would raise BrokenPipeError mid-crawl), the
  cancel flag is set, running threads get `join(timeout=10)`, then `os._exit(0)`.
  This is the cross-platform "die with the parent" mechanism — no PID files,
  no signals.
- **Environment**: the shell must pass `OPENROUTER_API_KEY` /
  `IOFAST_RESCUE_MODEL` (Tier 3 rescue) and any `IOFAST_CACHE_DIR` explicitly
  at spawn — a GUI-launched child does not inherit the user's shell profile.

## Methods

### `initialize`
Params: `{}`.
Result: `{"protocol": 1, "engine": "<__version__>", "manifest_schema": 1}`.
The shell calls this first and refuses to proceed on a protocol mismatch.

### `estimate`
Params: `{"url": str}`
Result: `{"page_count": int|null, "source": "sitemap"|"llms"|null,
"scope": str, "elapsed_ms": int}`.
The quick panel's pre-Enter pass. STATIC-ONLY under a HARD budget: two stdlib
GETs (`/sitemap.xml`, `/llms.txt`) raced in parallel with an 800ms server-side
wall clock — never a browser, never an LLM, never a nav parse; a sitemap
index is followed at most one child deep, budget permitting. Robots.txt is
skipped by design (two tiny well-known-file GETs; the crawl re-checks).
The panel abandons the call at 1s client-side; **Enter never waits on it** —
`scope` is derivable locally and the scrape validates its own inputs.
One estimate at a time (`-32000` while one runs); clients debounce.

### `map`
Params: `{"url": str, "scope": str|null}`
Result: the `map_run` result object verbatim (same JSON the CLI prints with
`--json`): `{pages, tree, scope, discovery, platform}`.
Runs in a worker thread. One map at a time — a second `map` while one runs
returns `-32000`. A map may run concurrently with a scrape (the quick panel
needs this). Not cancellable; note a map can legitimately take minutes on a
site with many sitemaps (25 fetches × 20s timeout worst case).

### `scrape`
Params: `{"url": str, "out": str, "scope": str|null, "depth": int=3,
"max_pages": int=200, "update": bool=false, "resume": bool=false,
"interactive": bool=false}`.
Optional `"env": {"OPENROUTER_API_KEY": str, "IOFAST_RESCUE_MODEL": str,
"IOFAST_PROXY": str}` — request-scoped secrets, applied to the process
environment for THIS run only and restored after (never argv, never a file;
keys outside the allowlist are `-32602`). The shell reads them from the OS
keychain and sends them only when a run may need them.
`IOFAST_PROXY` (e.g. `http://user:pass@host:port`) routes every fetch tier
(static/dynamic/stealth) through the resilient-fetch layer for sites that block
by IP. One value routes every tier through that proxy; several — separated by
commas, whitespace, or newlines — build one shared rotator cycled per fetch
across all tiers (cyclic, not failure-driven). Absent = no proxy, byte-identical
to before.
`update: true` is a conditional re-scrape: the previous `manifest.json` in
`out` supplies each page's stored validators (`etag` / `last_modified`,
additive schema-1 page fields), the static tier sends If-None-Match /
If-Modified-Since, and a 304 keeps the existing file, recorded `ok` with
reason "not modified (304)" — never a blind full re-crawl.
Update runs also carry an **overwrite guard**: when a page answers 200 but the
new content is under half of the manifest's recorded `chars` for that page
(the shape of a soft-block/consent page, which can clear the thin band with no
Cloudflare markers), the old file is kept untouched and the page is recorded
with the additive outcome `suspect_shrink` (a failure class — the run exits 1
and the page appears in `report.md`'s failures table). The suspect row keeps
the OLD validators, so the next update re-fetches and re-judges instead of
304-ing on suspect content.
`resume: true` replays a paused crawl's checkpoint frontier (see `cancel`):
it requires an existing `<out>/.crawl/checkpoint.pkl` (else `-32602`) and is
mutually exclusive with `update` (both set is `-32602`). The checkpoint carries
scrapling's pending queue but NOT the ledger, so the run seeds its ledger and
`seen` set from the prior `manifest.json` — the resumed run's manifest then
represents the WHOLE crawl and still reconciles (zero silent failures), instead
of undercounting to just the resumed tail. Unlike `update`, resume does NOT send
conditional GETs: it continues the frontier rather than re-validating fetched
pages.
`interactive: true` turns on the CAPTCHA handoff (PLAN Phase 4): when the
stealth tier meets a Cloudflare challenge its auto-solver cannot clear, the
engine opens a VISIBLE browser so the user solves it once, then continues the
fetch headless — reusing the clearance in a shared browser profile, pinning one
UA and one non-rotating proxy (cf_clearance binds both). A `human_required`
event announces the window before the wait, carrying `url` and `timeout` (the
human-solve budget in seconds); a `human_done` event carrying `url` and
`solved: bool` closes it on EVERY exit — solved, timed out, cancelled, or
raised. The pair is what a UI banner brackets on: the crawl fetches other pages
throughout the solve, so its own events keep arriving during the wait and cannot
mark its end. Cancel ends the wait at the next poll (≤1s); the CLI has no cancel channel, so a headed solve there ends only on solve or timeout. Default **false**: unattended and
scheduled runs never block on a human; they record the challenge as
`failed_fetch` exactly as before. The solved page is treated as a leaf — its
links are not followed. (Not yet validated against live Cloudflare — the manual
smoke `engine/tools/captcha_solve_smoke.py` is the gate before this is exposed
in the UI.)
`out` is **required** — a GUI-spawned sidecar has no meaningful cwd, so the
shell always supplies the library path. `depth` is clamped to [0, 10],
`max_pages` to [1, 2000]; out-of-range values clamp, wrong types are `-32602`.
Result: `{"written": int, "out_dir": str, "exit_code": int,
"cancelled": bool, "manifest": {…}}` — `out_dir` is absolute; `manifest` is
schema 1, unchanged. **The response is authoritative**; the terminal `result`
notification exists for CLI parity only.
One scrape at a time; a second `scrape` while one runs returns `-32000`.

Trust note: `out` is a client-supplied write path. The stdio pipe is the trust
boundary — anything that can reach it controls where files land. Acceptable
for a local shell-owned sidecar; do not expose this server on a socket.

### `push`
Params: `{"dir": str, "prompt": str, "model": str|null}` plus the same optional
`"env": {"OPENROUTER_API_KEY": str, "IOFAST_RESCUE_MODEL": str}` as `scrape`
(request-scoped, allowlisted, restored after the call — never argv, never a
file). Ingest Mode 3 (API push, PLAN Phase 3): the engine reads the run's
scraped `.md` pages (skipping the generated `report.md` / `README-UNTRUSTED.md`),
concatenates them under a char cap in filename order, and sends them to the
OpenRouter chat endpoint as UNTRUSTED reference alongside the user's `prompt`.
`model` is a plain param (not a secret); omitted, the engine defaults it from
`IOFAST_RESCUE_MODEL`. The system prompt marks the corpus untrusted, on top of
each page's own banner (PLAN rule). `dir` is a client-supplied read path — the
shell validates it is a library child before the call; the stdio pipe is the
same trust boundary as `scrape.out`.
Result: `{"digest": str, "model": str, "files": int, "chars": int,
"truncated": bool}` — `truncated` is true when the corpus hit the cap.
The answer is streamed from OpenRouter (`stream: true`): each token arrives as a
`push` event notification carrying a `delta` field, and the same full text is
returned in `digest` when the call lands. A `push` event with a `message` field
(no `delta`) is the one-shot pre-call progress line.
Runs in a worker thread. One push at a time (`-32000` while one runs); not
cancellable in protocol 1 (the streaming read runs to completion; a 120s
timeout bounds it). Config or corpus problems (missing key, no model, empty run)
and a bad upstream response (error envelope, empty answer) come back as `-32603`
with the message — never a bare `KeyError`. The key is read from the
request-scoped `env` and never written to `os.environ` (so a concurrent scrape's
env-wrap cannot race or persist it).

### `cancel`
Params: `{"rid": int|null}` — optionally pins the scrape request id, so a
cancel racing a finishing run cannot hit the next one. `-32001` on mismatch
or when no scrape is running.
Result: `{"cancelling": true, "rid": <id of the cancelled scrape>}`,
immediately. Idempotent: repeat calls return the same result and do NOT
re-invoke engine pause (a second engine-level pause force-stops mid-write).

Cancellation is cooperative, not instant:
- A `threading.Event` is checked between run phases (after preflight, after
  discovery/spider construction, before crawl start) — this covers the
  window where no engine exists yet and `Spider.pause()` would raise.
- Once the crawl runs, `Spider.pause()` requests graceful engine shutdown;
  `on_close` still executes, so ledger, report.md and manifest are written.
- Worst-case latency: a Tier 3 rescue call (blocking, 60s timeout) freezes
  the event loop; cancel can take up to a minute. The `cancel` response is
  immediate either way; the `scrape` response arrives when the run lands.

A cancelled run's ledger records every enqueued-but-unfetched URL with the
`cancelled` outcome (additive to `counts`, NOT in `FAILURE_OUTCOMES`), and
`manifest.info.cancelled = true`. `exit_code` keeps its CLI semantics and is
secondary to the `cancelled` flag — a cancel before anything was enqueued
still reports `exit_code: 3`; consult `cancelled` first.

Stop = pause + checkpoint: a paused crawl saves scrapling's
`checkpoint.pkl` under `<out>/.crawl`, and the ledger/report/manifest are
written regardless. There is no separate `resume` METHOD — `scrape` with
`resume: true` now replays that checkpoint frontier from where Stop left off,
seeding the ledger from the prior manifest so the whole crawl still reconciles
(see `scrape`). A fresh scrape of the same `out` (resume omitted) still
deliberately clears any stale checkpoint first (auto-resume would skip discovery
and this ledger is not checkpointed). The two "continue later" paths differ:
`resume: true` finishes an interrupted crawl's remaining frontier, while
`update: true` re-walks an already-complete site with conditional GETs so
already-fetched pages cost one 304 each.

### `browser_status`
Params: none.
Result: `{"installed": bool, "drivers": bool, "chromium": bool, "path": str}` —
the state of the out-of-band browser components under app support
(`IOFAST_SUPPORT_DIR` overrides the root for tests). `installed` is
`drivers AND chromium`; until it is true, every scrape runs static-only (the
frozen fast-path in `browsers_available()` answers False without spawning
anything). Answered synchronously.

### `install_browsers`
Params: none.
Downloads the playwright/patchright node drivers as SHA-256-pinned PyPI wheels
(`browser_pins.py`, regenerated by `packaging/gen_browser_pins.py` — the digests
are PyPI's own), extracts each wheel's `driver/` tree into
`<support>/driver/<package>-<version>/` atomically, then runs each driver's own
`install chromium` into `PLAYWRIGHT_BROWSERS_PATH`. A payload that does not
match its pinned digest is REFUSED and deleted — `-32603` with
`IntegrityError: checksum mismatch …`, nothing kept on disk. Progress arrives
as `browser_install` events (`component`, `stage`
`download|skip|extract|chromium|done`, `pct` on downloads, human `message`);
the chromium stage streams the driver's own output line by line (with a 900s
kill timeout), so download progress is never dark.
Result: the same shape as `browser_status`. Runs in a worker thread; one
install at a time (`-32000` while one runs). The freshly installed drivers are
active for the next scrape in the same process (driver resolution is repointed
at install time and re-checked per scrape).

Hardening: the install refuses to start with under ~2GB free disk; downloads
retry up to 3 times with backoff (safe — the digest check gates every
attempt); a package whose receipt already matches is skipped (`skip` stage),
so a re-run after a mid-install failure re-fetches only what is missing.
`IOFAST_PROXY` routes both the wheel download and the chromium fetch (exported
as `HTTPS_PROXY`/`HTTP_PROXY` to the driver child), and a
`PLAYWRIGHT_DOWNLOAD_HOST` set on the sidecar process passes through to the
chromium download. Each successful package writes an install receipt
(`<support>/components.json`: version, wheel digest, on-disk digests+sizes of
the driver files, chromium revision) — `browser_status` and the per-scrape
availability check trust receipt + file sizes (cheap), not bare file
existence, so a truncated or partial install reports not-installed.

Consent note for the app UI: a full install is ~770MB on disk, measured with
both pins at 1.58.x (~520MB chromium + ~250MB drivers) — show the real number
before starting.

### `verify_components`
Params: none.
Recomputes the SHA-256 of every receipt-recorded driver file (the honest
check the cheap size test skips — a same-size tamper only shows up here).
Result: `{"ok": bool, "packages": {name: "ok" | reason}, "status": …}` with
`status` in the `browser_status` shape. Read-only and answered synchronously
(~1s of hashing). To repair, call `install_browsers` again: its per-package
skip decision re-verifies digests, so anything this check flags gets
re-downloaded (`iofast browsers verify --repair` is the CLI one-step form).

## Notifications (event stream)

Every event the engine `emit()`s is forwarded verbatim as a notification:

```json
{"jsonrpc": "2.0", "method": "event", "params": {"type": "page", "...": "...", "rid": 7}}
```

`params` **is** the engine event dict — the existing shapes (`progress`,
`page`, `platform`, `warning`, `rescue`, `result`, `push`, `browser_install`,
`human_required`, `human_done`) are the contract, plus
one added field: `rid`, the id of the request that produced it (additive; the
CLI's `--json` stream is untouched — rid is injected by the RPC sink only).
Events from a `map` carry that map request's rid.

## Errors

| Code | Meaning |
|---|---|
| -32700 | unparseable line |
| -32600 | not a valid request object |
| -32601 | unknown method |
| -32602 | invalid params (bad URL, scope not a prefix, missing `out`, wrong types — validation shared with the CLI via `runner.validate`) |
| -32000 | method already running (one scrape, one map, one browser install) |
| -32001 | no scrape running / rid mismatch (cancel) |
| -32603 | internal error during a run (message carries the exception) |

Engine-level failures (pages that 403, thin content) are NOT errors — they
ride in the manifest and `exit_code`, same as the CLI.

## Threading model

- Main thread: blocking readline loop on stdin, dispatch.
- `map`/`scrape` each run in their own thread (max one of each); responses are
  written when the thread finishes. `cancel` and `initialize` are answered
  synchronously on the main thread.
- One process-wide lock serializes protocol writes.
- `discovery._get`'s per-host politeness delay is lock-protected so a
  concurrent map + scrape against the same host still space their requests.

## Contract test

`tests/test_rpc_session.py` drives a real in-process server with the runner
monkeypatched to replay canned events, asserting output lines byte-for-byte
(same serializer as production). Offline, deterministic, ordering included.
Beyond framing, the suite covers the lifecycle holes framing tests cannot
see: cancel during discovery (no engine yet), double cancel (idempotency),
EOF with a live run (bounded join, no-op sink), and type-error params
(`-32602`, not `-32603`).
