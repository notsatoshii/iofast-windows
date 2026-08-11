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
"max_pages": int=200, "update": bool=false}`.
Optional `"env": {"OPENROUTER_API_KEY": str, "IOFAST_RESCUE_MODEL": str}` —
request-scoped secrets for the Tier 3 rescue, applied to the process
environment for THIS run only and restored after (never argv, never a file;
keys outside the allowlist are `-32602`). The shell reads them from the OS
keychain and sends them only when a run may need the rescue.
`update: true` is a conditional re-scrape: the previous `manifest.json` in
`out` supplies each page's stored validators (`etag` / `last_modified`,
additive schema-1 page fields), the static tier sends If-None-Match /
If-Modified-Since, and a 304 keeps the existing file, recorded `ok` with
reason "not modified (304)" — never a blind full re-crawl.
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
written regardless. There is no `resume` METHOD in protocol 1 — a fresh
scrape of the same `out` deliberately clears any stale checkpoint first
(auto-resume would skip discovery and this ledger is not checkpointed);
the supported "continue later" path is `update: true`, which re-walks the
site with conditional GETs so already-fetched pages cost one 304 each.

## Notifications (event stream)

Every event the engine `emit()`s is forwarded verbatim as a notification:

```json
{"jsonrpc": "2.0", "method": "event", "params": {"type": "page", "...": "...", "rid": 7}}
```

`params` **is** the engine event dict — the existing shapes (`progress`,
`page`, `platform`, `warning`, `rescue`, `result`) are the contract, plus one
added field: `rid`, the id of the request that produced it (additive; the
CLI's `--json` stream is untouched — rid is injected by the RPC sink only).
Events from a `map` carry that map request's rid.

## Errors

| Code | Meaning |
|---|---|
| -32700 | unparseable line |
| -32600 | not a valid request object |
| -32601 | unknown method |
| -32602 | invalid params (bad URL, scope not a prefix, missing `out`, wrong types — validation shared with the CLI via `runner.validate`) |
| -32000 | method already running (one scrape, one map) |
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
