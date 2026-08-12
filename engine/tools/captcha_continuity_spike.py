"""Manual dev gate for slice #5 (CAPTCHA handoff). NOT run by CI.

Proves the SHELL-LEVEL mechanism #5 relies on: a Cloudflare challenge solved
once in a HEADED StealthySession can be continued by a later HEADLESS session,
because a shared persistent ``user_data_dir`` carries the clearance cookie. This
is a LOCAL echo server, not Cloudflare — it shows the mechanism is buildable,
NOT that Cloudflare accepts the replay (see "Does NOT prove" below).

Why UA parity matters, stated correctly (verified in scrapling _base.py:411-417):
cf_clearance binds UA. scrapling assigns the UA ASYMMETRICALLY —
  * headed + no explicit useragent -> Playwright's real Chromium build UA
  * headless + no explicit useragent -> a synthetic ``__default_useragent__``
    (a module-level constant from ``generate_headers``, regenerated once PER
    PROCESS, so it also drifts across separate sidecar launches).
So a challenge solved headed and continued headless presents two different UAs
by construction. #5 MUST construct BOTH sessions with one explicit ``useragent``
(a session-construction arg — ``_validators.py`` fetch params carry no UA field,
so a per-fetch override is impossible). Pinned => UA parity; unset => the
headed/headless asymmetry above. For #5 prefer capturing the HEADED session's
REAL build UA and reusing THAT verbatim for the headless continue, rather than a
hardcoded string: a hardcoded ``Chrome/149`` will mismatch chromium-1208's real
``sec-ch-ua`` client-hint version, which live Cloudflare cross-checks (this local
echo server does not).

Ground truth is the local echo server's REQUEST log (the wire, not the parsed
page):
  visit 1 (HEADED, fresh profile P1)   -> server Set-Cookie spike_clr, logs UA
  visit 2 (HEADLESS, SAME profile P1)  -> did the browser REPLAY spike_clr? UA?
  visit 3 (HEADLESS, DIFFERENT profile) -> negative control: must NOT replay,
                                           proving user_data_dir is the carrier
                                           (not some process-global cache).

PASS iff: cookie replayed in visit 2 AND cookie NOT replayed in visit 3
(control) AND UA identical across visits 1-2 AND no "Headless" token AND
navigator.webdriver parsed-and-falsey in both.

Does NOT prove (stays the manual smoke before #5 ships headed):
  * a real cf_clearance against LIVE Cloudflare;
  * IP stability — and note the app's OWN proxy rotation (PR #22) cycles the
    egress IP per fetch across all tiers, which alone invalidates an IP-bound
    cf_clearance. #5 MUST pin a single non-rotating proxy for the solve+continue
    pair;
  * cookie-attribute realism — real cf_clearance is Secure + HttpOnly +
    SameSite=None over HTTPS; this analog is a plain HTTP cookie;
  * deeper fingerprint parity (JA3/TLS, injected navigator fingerprint).

Run:
    engine/.venv/bin/python engine/tools/captcha_continuity_spike.py
Requirements: a headed display + patchright's pinned chromium (rev 1208). The app
delivers 1208 into its support dir (browsers.py); for a bare dev checkout point
PLAYWRIGHT_BROWSERS_PATH at a cache that has it, or symlink an installed build to
``chromium-1208`` in the default ms-playwright cache. If that binary is missing
the spike raises at visit 1 and reports INCONCLUSIVE — it does not silently pass.

Env knobs:
    SPIKE_RANDOM_UA=1   drop the UA pin -> reproduce the headed/headless break
    SPIKE_PIN_UA=<ua>    override the pinned UA (default: a fixed Chrome/macOS UA)
"""
import http.server
import json
import os
import re
import shutil
import socketserver
import sys
import threading
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, ".spike_profile")  # persistent user_data_dir under test
PROFILE_CTRL = os.path.join(HERE, ".spike_profile_ctrl")  # negative control
COOKIE = "spike_clr"

# Pin one UA across both sessions unless SPIKE_RANDOM_UA is set (which reproduces
# scrapling's headed/headless UA asymmetry, i.e. the break #5 must avoid).
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
PIN_UA = None if os.environ.get("SPIKE_RANDOM_UA") else (os.environ.get("SPIKE_PIN_UA") or DEFAULT_UA)

log = []  # server-observed ground truth, one entry per "/" request


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":  # ignore favicon/asset probes so log indexing stays honest
            self.send_response(404)
            self.end_headers()
            return
        cookie_hdr = self.headers.get("Cookie", "")
        replayed = COOKIE in cookie_hdr
        log.append({"ua": self.headers.get("User-Agent", ""), "cookie": cookie_hdr, "replayed": replayed})
        body = (
            "<html><body><div id='out'></div><script>"
            "document.getElementById('out').textContent = JSON.stringify({"
            "ua: navigator.userAgent, webdriver: navigator.webdriver,"
            "platform: navigator.platform});"
            "</script></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        if not replayed:  # first visit on a profile mints the clearance-analog cookie
            self.send_header(
                "Set-Cookie", f"{COOKIE}={uuid.uuid4().hex}; Max-Age=86400; Path=/; SameSite=Lax"
            )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def navigator_from(resp):
    """Pull the JS-reported navigator JSON out of the rendered #out div.

    Returns None on a render/parse miss so callers can FAIL rather than read
    missing keys as clean (a None webdriver is falsey and would pass vacuously).
    """
    html = getattr(resp, "html_content", None) or getattr(resp, "body", "") or str(resp)
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    m = re.search(r'id=[\'"]out[\'"][^>]*>(\{.*?\})<', html) or re.search(r'(\{[^{}]*"ua"[^{}]*\})', html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def visit(headless, url, profile):
    from scrapling.fetchers import StealthySession

    kw = dict(headless=headless, user_data_dir=profile, solve_cloudflare=False, max_pages=1)
    if PIN_UA:
        kw["useragent"] = PIN_UA
    with StealthySession(**kw) as s:
        return navigator_from(s.fetch(url))


def main():
    for p in (PROFILE, PROFILE_CTRL):
        shutil.rmtree(p, ignore_errors=True)
        os.makedirs(p, exist_ok=True)

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print(f"[spike] echo server on {url}  (UA {'PINNED' if PIN_UA else 'UNSET -> headed/headless asymmetry'})", flush=True)
    try:
        print("[spike] visit 1: HEADED, profile P1 (fresh) ...", flush=True)
        nav_headed = visit(False, url, PROFILE)
        print(f"[spike]   navigator(headed) = {nav_headed}", flush=True)
        print("[spike] visit 2: HEADLESS, profile P1 (same) ...", flush=True)
        nav_headless = visit(True, url, PROFILE)
        print(f"[spike]   navigator(headless) = {nav_headless}", flush=True)
        print("[spike] visit 3: HEADLESS, profile P2 (control, must NOT replay) ...", flush=True)
        visit(True, url, PROFILE_CTRL)
    finally:
        httpd.shutdown()

    hits = list(log)  # only "/" requests are logged
    if len(hits) < 3:
        print(f"\n[spike] RESULT: INCONCLUSIVE — {len(hits)} server hit(s), need 3: {hits}")
        return 2
    v1, v2, v3 = hits[0], hits[1], hits[2]

    checks = [
        ("cookie replayed headed->headless on shared profile", v2["replayed"]),
        ("control: cookie NOT replayed on a different profile", not v3["replayed"]),
        ("UA identical headed vs headless", v1["ua"] == v2["ua"] and v1["ua"] != ""),
        ("no 'Headless' token in either UA", "Headless" not in v1["ua"] and "Headless" not in v2["ua"]),
        (
            "navigator.webdriver parsed-and-falsey in both",
            isinstance(nav_headed, dict) and "webdriver" in nav_headed and not nav_headed["webdriver"]
            and isinstance(nav_headless, dict) and "webdriver" in nav_headless and not nav_headless["webdriver"],
        ),
    ]
    print("\n[spike] === checks ===")
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n[spike] UA headed   : {v1['ua']}")
    print(f"[spike] UA headless : {v2['ua']}")
    print(f"[spike] cookie sent by headless (P1): {v2['cookie']!r}")
    print(f"[spike] cookie sent by control (P2): {v3['cookie']!r}")

    passed = all(ok for _, ok in checks)
    print(f"\n[spike] RESULT: {'PASS — shell mechanism sound (pin the UA + share user_data_dir; pin a single proxy)' if passed else 'FAIL — #5 blocked'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
