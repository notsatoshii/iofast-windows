"""Acceptance gate for the frozen sidecar: proves the freeze broke neither
stdio JSON-RPC nor the static fetch stack.

Usage: python packaging/verify_frozen.py dist/iofast/iofast

Drives the REAL frozen binary over stdio:
1. initialize            -> protocol 1 (framing survived the freeze)
2. bad params            -> -32602 (validation, not a crash)
3. unparseable line      -> -32700
4. map + scrape of a local fixture docs site (http.server on 127.0.0.1) —
   exercises curl_cffi/scrapling static fetch, lxml, trafilatura, the writer.
   This is the check that catches missing lazy imports in the bundle.
5. cancel mid-crawl of a delayed, link-chained fixture (each page is only
   discoverable from the previous one, so the crawl is sequential and
   genuinely interruptible) -> cancelled run + checkpoint.pkl on disk.
6. scrape resume=true    -> replays the checkpoint frontier to completion.
7. scrape update=true    -> every page answers 304 off the stored validators;
   the files on disk are byte- and mtime-identical afterwards.
8. push                  -> streamed digest from a local OpenRouter stand-in
   (via the IOFAST_OPENROUTER_URL test seam), Authorization header carries
   the request-scoped key.
9. update overwrite guard -> one chain page mutates into a consent/soft-block
   page (200, no CF markers, clears the thin band) and one into an honest
   trim. The guard must refuse the first (old file kept byte-identical,
   outcome suspect_shrink, exit 1) and write the second.
10. browser components -> with a fresh (empty) support dir, browser_status
   reports nothing installed. On macOS the scrape above warned "static tier
   only": the frozen fast-path answers False without spawning any driver. On
   Windows it must NOT warn: the bundled driver launches system Edge/Chrome.
11. CLI entry point -> a frozen `iofast scrape --json` run (the second frozen
   entry point, one gate assertion per entry point) roots its browser lookup
   in app support. On macOS the static-tier warning names <support>/browsers,
   proving default_storage_env() ran for the scrape command, not just
   `browsers`. On Windows the scrape finds system Edge/Chrome (no warning) and
   the frozen `browsers status` reports the same support dir.
Every stdout line must parse as JSON: any dependency print() that leaked onto
fd 1 fails the run (the RPC server redirects fd 1 to stderr — proven here).
"""

import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

# {title} sits inside the prose on purpose: extraction drops the <h1>, and the
# writer saves identical page bodies once (later ones record "duplicate").
PAGE = """<!doctype html><html><head><title>{title}</title></head><body>
<nav><a href="/docs/">Home</a><a href="/docs/guide">Guide</a></nav>
<main><h1>{title}</h1>
<p>{title}: this fixture page carries enough real prose to clear the thin-page band:
it explains, at comfortable length, that the frozen sidecar must fetch,
extract, convert and write this content without any browser tier. The
extraction stack (trafilatura with an lxml fallback) needs a believable
document, so here is a second paragraph with plain declarative sentences
describing nothing in particular at all, but at length and in clean HTML.</p>
<p>A third paragraph keeps the character count safely above the escalation
threshold so the static tier finishes the job on its own, which is exactly
the property this script exists to prove about the bundle.</p>
</main></body></html>"""

CHAIN_PAGE = """<!doctype html><html><head><title>{title}</title></head><body>
<nav>{next_link}</nav>
<main><h1>{title}</h1>
<p>{title}: this chain fixture page exists so the crawl is sequential: the only route
to the next page is the single link above, which means the spider cannot see
page N+1 before it has fetched and parsed page N. That property is what makes
the cancel test deterministic — a cancel sent after the first page event is
guaranteed to leave undiscovered frontier behind for resume to replay.</p>
<p>Like the docs fixture, this paragraph and the next keep the character
count safely above the thin-page escalation band so the static tier handles
everything and the frozen bundle needs no browser to pass this gate.</p>
<p>A third paragraph of plain declarative filler, present for length alone,
closing out a believable documentation-shaped page in clean HTML.</p>
</main></body></html>"""

DOCS = {
    "/docs/": PAGE.format(title="Fixture Docs Home"),
    "/docs/guide": PAGE.format(title="Fixture Guide"),
}

CHAIN_LEN = 8  # /chain/ index + p1..p7
CHAIN_LAST_MODIFIED = "Wed, 01 Jan 2025 00:00:00 GMT"


# Windows always has Edge, which the frozen patchright driver launches without
# any managed components; macOS has no such system channel.
SYSTEM_BROWSER = sys.platform == "win32"


def chain_paths():
    return ["/chain/"] + [f"/chain/p{i}" for i in range(1, CHAIN_LEN)]


def chain_page(index):
    paths = chain_paths()
    if index + 1 < CHAIN_LEN:
        next_link = f'<a href="{paths[index + 1]}">Next</a>'
    else:
        next_link = ""
    return CHAIN_PAGE.format(title=f"Chain {index}", next_link=next_link)


CHAIN = {path: chain_page(i) for i, path in enumerate(chain_paths())}

MUTATED_LAST_MODIFIED = "Thu, 01 Jan 2026 00:00:00 GMT"

# A soft-block/consent page: 200, no Cloudflare markers, extracts past the
# thin band (>= 250 chars) — everything upstream of the overwrite guard lets
# it through. The reason string of the suspect row proves the measured size.
SOFT_BLOCK_PAGE = """<!doctype html><html><head><title>One more step</title></head><body>
<main><h1>Before you continue</h1>
<p>We value your privacy and your reading experience. Please review our terms
of service and confirm your consent preferences before you continue to the
requested documentation page. Click accept to proceed with all cookies
enabled, or open the preference manager to make a granular choice.</p>
</main></body></html>"""

# An honest editorial trim of a chain page: shorter than the recorded page but
# well above half of it — the guard must let this overwrite happen.
HONEST_TRIM_PAGE = """<!doctype html><html><head><title>{title}</title></head><body>
<nav>{next_link}</nav>
<main><h1>{title}</h1>
<p>This chain fixture page was legitimately rewritten shorter in the second
revision: the prose now spans two paragraphs instead of three, which is an
honest editorial trim that any real documentation site performs routinely.</p>
<p>The remaining paragraph keeps the character count above the thin-page
escalation band so the static tier still writes it, while landing well above
half of the recorded size — the overwrite guard must let this through.</p>
</main></body></html>"""

# Two SSE deltas + [DONE]: the shape push.py streams from OpenRouter.
SSE_BODY = (
    'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"world"}}]}\n\n'
    "data: [DONE]\n\n"
)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/sitemap.xml":
            # Only the /docs/ pages: /chain/ stays sitemap-invisible so a
            # scrape scoped there falls back to the sequential link crawl.
            urls = "".join(
                f"<url><loc>http://{self.headers['Host']}{path}</loc></url>" for path in DOCS
            )
            body = f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
            self._send(200, body, "application/xml")
            return
        key = self.path if self.path in DOCS or self.path in CHAIN else self.path.rstrip("/") + "/"
        if key in CHAIN:
            # The engine normalizes trailing slashes; serve both spellings.
            time.sleep(self.server.delay)
            mutated = self.server.mutated.get(key)
            if mutated is not None:
                # Changed content: conditionals no longer match, answer 200.
                self._send(200, mutated, "text/html", last_modified=MUTATED_LAST_MODIFIED)
                return
            if self.headers.get("If-Modified-Since"):
                self.send_response(304)
                self.end_headers()
                return
            self._send(200, CHAIN[key], "text/html", last_modified=CHAIN_LAST_MODIFIED)
        elif key in DOCS:
            self._send(200, DOCS[key], "text/html")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path == "/api/chat":
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.server.push_auth_headers.append(self.headers.get("Authorization"))
            data = SSE_BODY.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send(404, "not found", "text/plain")

    def _send(self, status, body, ctype, last_modified=None):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if last_modified:
            self.send_header("Last-Modified", last_modified)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def page_files(out_dir):
    """The run's scraped pages: every .md except the generated pair."""
    skip = {"report.md", "README-UNTRUSTED.md"}
    return sorted(
        name
        for name in os.listdir(out_dir)
        if name.endswith(".md") and name not in skip
    )


def file_stats(out_dir):
    """(bytes, mtime_ns) per page file — the 'update kept the file' evidence."""
    stats = {}
    for name in page_files(out_dir):
        path = os.path.join(out_dir, name)
        info = os.stat(path)
        with open(path, "rb") as fh:
            stats[name] = (fh.read(), info.st_mtime_ns)
    return stats


def main(binary, *, dev=False):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.delay = 0.0
    server.push_auth_headers = []
    server.mutated = {}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    out_docs = tempfile.mkdtemp(prefix="iofast-frozen-docs-")
    out_chain = tempfile.mkdtemp(prefix="iofast-frozen-chain-")
    support = tempfile.mkdtemp(prefix="iofast-frozen-support-")

    # stdin must stay OPEN until the last response: EOF is the shutdown signal
    # (parent gone -> sink silenced), so closing early swallows in-flight replies.
    proc = subprocess.Popen(
        [binary, "rpc"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "IOFAST_OPENROUTER_URL": f"{base}/api/chat",
            "IOFAST_SUPPORT_DIR": support,
        },
    )
    failures = []
    messages = []
    raw = []

    def reader():
        for line in proc.stdout:
            raw.append(line.rstrip("\n"))
            try:
                messages.append(json.loads(line))
            except ValueError:
                failures.append(f"non-JSON line on protocol stdout: {line!r}")

    threading.Thread(target=reader, daemon=True).start()

    def send(request):
        proc.stdin.write(
            (request if isinstance(request, str) else json.dumps(request)) + "\n"
        )
        proc.stdin.flush()

    def wait_for(predicate, label, timeout=150):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in list(messages):
                if predicate(message):
                    return message
            time.sleep(0.05)
        failures.append(f"timed out waiting for: {label}")
        return None

    def reply(rid):
        return wait_for(lambda m: m.get("id") == rid, f"response id {rid}")

    def expect(cond, label):
        (failures.append if not cond else (lambda _: None))(label)
        print(("ok  " if cond else "FAIL") + f"  {label}")

    # --- 1-4: framing, validation, map, plain scrape (the original gate) ---
    for request in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "scrape", "params": {"url": "nope", "out": out_docs}},
        "garbage not json",
        {"jsonrpc": "2.0", "id": 3, "method": "map", "params": {"url": f"{base}/docs/"}},
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "scrape",
            "params": {"url": f"{base}/docs/", "out": out_docs, "max_pages": 10},
        },
    ]:
        send(request)

    r1, r2, r4 = reply(1), reply(2), reply(4)
    expect((r1 or {}).get("result", {}).get("protocol") == 1, "initialize -> protocol 1")
    # Build provenance: the bundle must be frozen from an ancestor of HEAD with
    # everything that determines bundle contents unchanged since — including
    # UNCOMMITTED edits (git diff against the WORKING TREE, not HEAD) and
    # untracked files. A stale bundle (the F1 failure class) fails HERE by
    # name instead of on whatever feature drifted.
    build = (r1 or {}).get("result", {}).get("build") or ""
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    BUNDLE_INPUTS = ("engine/src", "engine/packaging", "engine/pyproject.toml")

    def git_ok(*args):
        return subprocess.run(["git", "-C", repo, *args], capture_output=True).returncode == 0

    # The stamp is judged against THIS script's repo — so the binary must live
    # in the same tree, or a bundle copied from another checkout would be
    # validated against the wrong history and read ok.
    binary_in_repo = os.path.realpath(os.path.abspath(binary)).startswith(
        os.path.realpath(repo) + os.sep
    )
    untracked = subprocess.run(
        ["git", "-C", repo, "-c", "status.showUntrackedFiles=normal", "status",
         "--porcelain", "-uall", "--", *BUNDLE_INPUTS],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dev:
        # --dev mode: skip git provenance (dirty tree during active development).
        # All functional tests still run; only the build-stamp ancestry check
        # is waived, for validating a just-built sidecar by hand mid-change.
        print(f"[--dev] skipping git-clean provenance check (build={build[:12] or 'unknown'})")
    else:
        expect(
            binary_in_repo
            and re.fullmatch(r"[0-9a-f]{40}", build) is not None
            and git_ok("merge-base", "--is-ancestor", build, "HEAD")
            and git_ok("diff", "--quiet", build, "--", *BUNDLE_INPUTS)
            and not untracked,
            f"build stamp {build[:12] or None!r} is an ancestor of HEAD with bundle inputs "
            f"(src/packaging/pyproject) unchanged, tree clean there, binary inside this repo"
            + ("" if binary_in_repo else f" (binary {binary!r} is OUTSIDE {repo!r})"),
        )
    expect((r2 or {}).get("error", {}).get("code") == -32602, "bad url -> -32602")
    expect(
        any(m.get("error", {}).get("code") == -32700 for m in messages if m.get("id") is None),
        "garbage line -> -32700",
    )
    r3 = reply(3)
    expect((r3 or {}).get("result", {}).get("pages") is not None, "map returns pages")
    scrape = (r4 or {}).get("result", {})
    expect(scrape.get("exit_code") == 0, f"scrape exit_code 0 (got {scrape.get('exit_code')})")
    expect(scrape.get("written") == 2, f"scrape wrote 2 pages (got {scrape.get('written')})")
    expect(scrape.get("manifest", {}).get("schema") == 1, "manifest schema 1")
    # quality.py is a new module reached only through writer.write_run — if the
    # freeze missed it, every scrape this bundle runs would raise at the end.
    # The verdict itself is not pinned to OK: these fixture pages measure 731
    # chars, under quality.THIN_FLOOR, so SUSPECT is the correct reading of them.
    quality = scrape.get("manifest", {}).get("quality", {})
    expect(
        quality.get("verdict") in ("OK", "SUSPECT", "unrated")
        and quality.get("pages_ok") == 2,
        f"quality verdict present and counted (got {quality.get('verdict')!r}, "
        f"pages_ok={quality.get('pages_ok')})",
    )
    expect(
        scrape.get("manifest", {}).get("info", {}).get("sourcing", "").endswith("via HTML extract"),
        "manifest info carries the sourcing counters",
    )
    expect(
        any(m.get("method") == "event" and m["params"].get("rid") == 4 for m in messages),
        "scrape events carry rid",
    )

    # --- 5: cancel mid-crawl. The chain is sequential (page N+1 is only
    # discoverable from page N) and each chain response sleeps, so a cancel
    # sent after the first page event always lands with frontier remaining. ---
    server.delay = 0.4
    send(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "scrape",
            "params": {"url": f"{base}/chain/", "out": out_chain, "max_pages": 20},
        }
    )
    wait_for(
        lambda m: m.get("method") == "event"
        and m["params"].get("rid") == 5
        and m["params"].get("type") == "page",
        "first page event of the chain scrape",
    )
    send({"jsonrpc": "2.0", "id": 6, "method": "cancel", "params": {"rid": 5}})
    r6 = reply(6)
    expect(
        (r6 or {}).get("result", {}).get("cancelling") is True,
        "cancel -> cancelling: true",
    )
    r5 = reply(5)
    cancelled = (r5 or {}).get("result", {})
    expect(cancelled.get("cancelled") is True, "cancelled scrape reports cancelled: true")
    first_written = cancelled.get("written")
    expect(
        isinstance(first_written, int) and 1 <= first_written < CHAIN_LEN,
        f"cancel left pages unwritten ({first_written} of {CHAIN_LEN})",
    )
    checkpoint = os.path.join(out_chain, ".crawl", "checkpoint.pkl")
    expect(os.path.exists(checkpoint), "cancel saved .crawl/checkpoint.pkl")

    # --- 6: resume replays the checkpoint frontier to completion. ---
    server.delay = 0.0
    send(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "scrape",
            "params": {"url": f"{base}/chain/", "out": out_chain, "resume": True},
        }
    )
    r7 = reply(7)
    resumed = (r7 or {}).get("result", {})
    expect(resumed.get("exit_code") == 0, f"resume exit_code 0 (got {resumed.get('exit_code')})")
    expect(resumed.get("cancelled") is not True, "resume run is not cancelled")
    on_disk = page_files(out_chain)
    expect(
        len(on_disk) == CHAIN_LEN,
        f"resume completed the chain ({len(on_disk)} of {CHAIN_LEN} pages on disk)",
    )
    resumed_pages = resumed.get("manifest", {}).get("pages", [])
    expect(
        len([p for p in resumed_pages if p.get("file")]) == CHAIN_LEN,
        "resumed manifest carries all chain pages (no undercount)",
    )

    # --- 7: update answers 304 everywhere and keeps every file untouched. ---
    before = file_stats(out_chain)
    send(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "scrape",
            "params": {"url": f"{base}/chain/", "out": out_chain, "update": True},
        }
    )
    r8 = reply(8)
    updated = (r8 or {}).get("result", {})
    expect(updated.get("exit_code") == 0, f"update exit_code 0 (got {updated.get('exit_code')})")
    expect(
        updated.get("written") == 0,
        f"update rewrote nothing (written {updated.get('written')})",
    )
    updated_pages = updated.get("manifest", {}).get("pages", [])
    all_304 = [p for p in updated_pages if p.get("status") == 304]
    expect(
        len(all_304) == CHAIN_LEN,
        f"update: every page answered 304 ({len(all_304)} of {CHAIN_LEN})",
    )
    after = file_stats(out_chain)
    expect(
        before == after,
        "update kept every previous file (bytes and mtime identical)",
    )

    # --- 8: push streams a digest from the fixture LLM endpoint. ---
    send(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "push",
            "params": {
                "dir": out_chain,
                "prompt": "Summarize the chain.",
                "model": "fixture-model",
                "env": {"OPENROUTER_API_KEY": "test-key-123"},
            },
        }
    )
    r9 = reply(9)
    push = (r9 or {}).get("result", {})
    expect(push.get("digest") == "Hello world", f"push digest streamed (got {push.get('digest')!r})")
    expect(push.get("files") == CHAIN_LEN, f"push read the corpus ({push.get('files')} files)")
    expect(
        any(
            m.get("method") == "event"
            and m["params"].get("type") == "push"
            and m["params"].get("delta")
            for m in messages
        ),
        "push deltas arrived as events",
    )
    expect(
        server.push_auth_headers == ["Bearer test-key-123"],
        "push sent the request-scoped key in the Authorization header",
    )

    # --- 9: update overwrite guard. One page mutates into a consent/soft-block
    # page (refused: old file kept, suspect_shrink, exit 1) and one into an
    # honest trim (written normally). Fixture preconditions are asserted from
    # the run's own measured numbers, not remembered constants. ---
    suspect_url, trim_url = f"{base}/chain/p3", f"{base}/chain/p5"
    server.mutated["/chain/p3"] = SOFT_BLOCK_PAGE
    server.mutated["/chain/p5"] = HONEST_TRIM_PAGE.format(
        title="Chain 5", next_link=f'<a href="{chain_paths()[6]}">Next</a>'
    )
    prev_rows = {p["url"]: p for p in updated_pages if p.get("file")}
    before_guard = file_stats(out_chain)
    send(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "scrape",
            "params": {"url": f"{base}/chain/", "out": out_chain, "update": True},
        }
    )
    r10 = reply(10)
    guarded = (r10 or {}).get("result", {})
    expect(
        guarded.get("exit_code") == 1,
        f"guard run exits 1 (suspect page counts as failure; got {guarded.get('exit_code')})",
    )
    expect(
        guarded.get("written") == 1,
        f"guard run wrote only the honest trim (written {guarded.get('written')})",
    )
    rows = {p["url"]: p for p in guarded.get("manifest", {}).get("pages", [])}
    suspect = rows.get(suspect_url, {})
    prev_suspect = prev_rows.get(suspect_url, {})
    expect(
        suspect.get("outcome") == "suspect_shrink",
        f"consent shrink refused as suspect_shrink (got {suspect.get('outcome')!r})",
    )
    expect(
        suspect.get("file") == prev_suspect.get("file")
        and suspect.get("chars") == prev_suspect.get("chars"),
        "suspect row keeps the previous file and recorded chars",
    )
    new_chars = re.search(r"new content (\d+) chars", suspect.get("reason", ""))
    prev_chars = prev_suspect.get("chars") or 0
    expect(
        new_chars is not None
        and 250 <= int(new_chars.group(1)) < prev_chars * 0.45,
        f"fixture precondition: consent page clears the thin band and sits well "
        f"inside the refuse band (reason {suspect.get('reason')!r} vs {prev_chars})",
    )
    trim = rows.get(trim_url, {})
    prev_trim_chars = (prev_rows.get(trim_url, {}).get("chars")) or 0
    expect(
        trim.get("outcome") == "ok"
        and prev_trim_chars * 0.55 < (trim.get("chars") or 0) < prev_trim_chars,
        f"fixture precondition + pass: honest trim rewrote the page "
        f"({trim.get('chars')} of {prev_trim_chars} chars, outcome {trim.get('outcome')!r})",
    )
    after_guard = file_stats(out_chain)
    suspect_file = prev_suspect.get("file", "")
    expect(
        before_guard.get(suspect_file) == after_guard.get(suspect_file),
        "guard kept the suspect page's file (bytes and mtime identical)",
    )
    trim_file = trim.get("file", "")
    expect(
        trim_file
        and before_guard.get(trim_file) != after_guard.get(trim_file)
        and b"honest editorial trim" in (after_guard.get(trim_file) or (b"", 0))[0],
        "honest trim landed on disk",
    )
    expect(
        len([p for p in rows.values() if p.get("status") == 304]) == CHAIN_LEN - 2,
        "guard run: every untouched page still answered 304",
    )

    # --- 10: browser components absent -> honest status, no driver spawn. ---
    send({"jsonrpc": "2.0", "id": 11, "method": "browser_status"})
    r11 = reply(11)
    components = (r11 or {}).get("result", {})
    expect(
        components.get("installed") is False and components.get("drivers") is False,
        f"browser_status: fresh support dir reports nothing installed (got {components})",
    )
    expect(
        components.get("path") == support,
        "browser_status: components rooted in IOFAST_SUPPORT_DIR",
    )
    static_only = any(
        m.get("method") == "event"
        and m["params"].get("rid") == 4
        and m["params"].get("type") == "warning"
        and "static tier only" in (m["params"].get("message") or "")
        for m in messages
    )
    if SYSTEM_BROWSER:
        # Windows ships Edge: the bundled driver launches it with no managed
        # components, so the browser tiers stay on.
        expect(
            not static_only,
            "frozen scrape found system Edge/Chrome with nothing installed (no static-tier warning)",
        )
    else:
        expect(static_only, "frozen scrape warned 'static tier only' (fast-path stayed False)")

    # --- 11: the CLI entry point sets storage env before scraping. ---
    cli_out = tempfile.mkdtemp(prefix="iofast-frozen-cli-")
    cli_support = tempfile.mkdtemp(prefix="iofast-frozen-cli-support-")
    cli_run = subprocess.run(
        [binary, "scrape", f"{base}/docs/", "--out", cli_out, "--json", "--max-pages", "3"],
        capture_output=True,
        text=True,
        timeout=150,
        env={**os.environ, "IOFAST_SUPPORT_DIR": cli_support},
    )
    expect(cli_run.returncode == 0, f"CLI scrape exit 0 (got {cli_run.returncode})")
    cli_events = []
    for line in cli_run.stdout.splitlines():
        try:
            cli_events.append(json.loads(line))
        except ValueError:
            failures.append(f"non-JSON line on CLI --json stdout: {line!r}")
    warning = next(
        (
            e.get("message", "")
            for e in cli_events
            if e.get("type") == "warning" and "static tier only" in e.get("message", "")
        ),
        "",
    )
    if SYSTEM_BROWSER:
        # No static-tier warning names the path when Edge launches, so prove
        # the CLI prelude (default_storage_env, run before every command)
        # rooted the same support dir through the frozen `browsers status`.
        expect(not warning, f"CLI scrape found system Edge/Chrome (warning: {warning!r})")
        cli_status = subprocess.run(
            [binary, "browsers", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "IOFAST_SUPPORT_DIR": cli_support},
        )
        try:
            cli_components = json.loads(cli_status.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            cli_components = {}
        expect(
            cli_components.get("path") == cli_support,
            f"CLI looked for browser components under app support (got {cli_components})",
        )
    else:
        expect(
            os.path.join(cli_support, "browsers") in warning,
            f"CLI scrape looked for chromium under app support (warning: {warning!r})",
        )

    proc.stdin.close()  # EOF -> clean shutdown
    proc.wait(timeout=30)
    stderr_text = proc.stderr.read()

    server.shutdown()
    if failures:
        print("\nprotocol lines:\n" + "\n".join(raw[-10:]))
        print("\nstderr tail:\n" + "\n".join(stderr_text.splitlines()[-15:]))
        sys.exit(1)
    print(
        "\nfrozen sidecar verified: stdio protocol, static fetch, "
        "cancel/resume/update lifecycle, overwrite guard, browser-component "
        "status, CLI storage env and push intact"
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Verify frozen iofast sidecar")
    parser.add_argument("binary", help="Path to frozen iofast executable")
    parser.add_argument(
        "--dev", action="store_true",
        help="Skip git-clean provenance check (for active development trees)",
    )
    args = parser.parse_args()
    main(args.binary, dev=args.dev)
