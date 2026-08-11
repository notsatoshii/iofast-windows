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
Every stdout line must parse as JSON: any dependency print() that leaked onto
fd 1 fails the run (the RPC server redirects fd 1 to stderr — proven here).
"""

import http.server
import json
import subprocess
import sys
import tempfile
import threading
import time

PAGE = """<!doctype html><html><head><title>{title}</title></head><body>
<nav><a href="/docs/">Home</a><a href="/docs/guide">Guide</a></nav>
<main><h1>{title}</h1>
<p>This fixture page carries enough real prose to clear the thin-page band:
it explains, at comfortable length, that the frozen sidecar must fetch,
extract, convert and write this content without any browser tier. The
extraction stack (trafilatura with an lxml fallback) needs a believable
document, so here is a second paragraph with plain declarative sentences
describing nothing in particular at all, but at length and in clean HTML.</p>
<p>A third paragraph keeps the character count safely above the escalation
threshold so the static tier finishes the job on its own, which is exactly
the property this script exists to prove about the bundle.</p>
</main></body></html>"""

DOCS = {
    "/docs/": PAGE.format(title="Fixture Docs Home"),
    "/docs/guide": PAGE.format(title="Fixture Guide"),
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/sitemap.xml":
            urls = "".join(
                f"<url><loc>http://{self.headers['Host']}{path}</loc></url>" for path in DOCS
            )
            body = f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
            self._send(200, body, "application/xml")
        elif self.path.rstrip("/") + "/" in DOCS or self.path in DOCS:
            # The engine normalizes trailing slashes; serve both spellings.
            key = self.path if self.path in DOCS else self.path.rstrip("/") + "/"
            self._send(200, DOCS[key], "text/html")
        else:
            self._send(404, "not found", "text/plain")

    def _send(self, status, body, ctype):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def main(binary):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    out_dir = tempfile.mkdtemp(prefix="iofast-frozen-")

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "scrape", "params": {"url": "nope", "out": out_dir}},
        "garbage not json",
        {"jsonrpc": "2.0", "id": 3, "method": "map", "params": {"url": f"{base}/docs/"}},
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "scrape",
            "params": {"url": f"{base}/docs/", "out": out_dir, "max_pages": 10},
        },
    ]
    # stdin must stay OPEN until the last response: EOF is the shutdown signal
    # (parent gone -> sink silenced), so closing early swallows in-flight replies.
    proc = subprocess.Popen(
        [binary, "rpc"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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
    for request in requests:
        proc.stdin.write((request if isinstance(request, str) else json.dumps(request)) + "\n")
    proc.stdin.flush()

    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if any(m.get("id") == requests[-1]["id"] for m in messages):
            break
        time.sleep(0.2)
    else:
        failures.append("timed out waiting for the final scrape response")
    proc.stdin.close()  # EOF -> clean shutdown
    proc.wait(timeout=30)
    stderr_text = proc.stderr.read()

    by_id = {m["id"]: m for m in messages if "id" in m and m["id"] is not None}

    def expect(cond, label):
        (failures.append if not cond else (lambda _: None))(label)
        print(("ok  " if cond else "FAIL") + f"  {label}")

    expect(by_id.get(1, {}).get("result", {}).get("protocol") == 1, "initialize -> protocol 1")
    expect(by_id.get(2, {}).get("error", {}).get("code") == -32602, "bad url -> -32602")
    expect(
        any(m.get("error", {}).get("code") == -32700 for m in messages if m.get("id") is None),
        "garbage line -> -32700",
    )
    expect(by_id.get(3, {}).get("result", {}).get("pages") is not None, "map returns pages")
    scrape = by_id.get(4, {}).get("result", {})
    expect(scrape.get("exit_code") == 0, f"scrape exit_code 0 (got {scrape.get('exit_code')})")
    expect(scrape.get("written") == 2, f"scrape wrote 2 pages (got {scrape.get('written')})")
    expect(scrape.get("manifest", {}).get("schema") == 1, "manifest schema 1")
    expect(
        any(m.get("method") == "event" and m["params"].get("rid") == 4 for m in messages),
        "scrape events carry rid",
    )

    server.shutdown()
    if failures:
        print("\nprotocol lines:\n" + "\n".join(raw[-10:]))
        print("\nstderr tail:\n" + "\n".join(stderr_text.splitlines()[-15:]))
        sys.exit(1)
    print("\nfrozen sidecar verified: stdio protocol + static fetch stack intact")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: verify_frozen.py <path-to-frozen-iofast>")
    main(sys.argv[1])
