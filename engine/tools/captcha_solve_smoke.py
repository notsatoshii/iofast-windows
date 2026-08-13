"""Manual LIVE-Cloudflare smoke for slice #5 (CAPTCHA handoff). NOT run by CI.

This is the gate owed before `interactive` is exposed in the UI / flipped on. It
drives the REAL path — ``solve.solve_and_continue`` against a live
Cloudflare-protected URL — so a human confirms what the offline tests and the
local-echo spike cannot: that Cloudflare ACCEPTS the headed-solve→headless-replay.

What a PASS here establishes (none provable offline):
  * a real cf_clearance is minted by the headed solve and REPLAYED by the
    headless continue on the shared profile (continue returns 200, no challenge);
  * headed→headless fingerprint parity holds (WebGL/GPU renderer, the injected
    navigator fingerprint) — a mismatch makes Cloudflare re-challenge the continue;
  * the pinned UA matches chromium-1208's real ``sec-ch-ua`` client-hint;
  * the egress IP is stable across the pair (pass IOFAST_PROXY to pin one proxy).

Run (a HEADED display + patchright's chromium rev 1208 are required — the app
delivers 1208, or symlink an installed build to ``chromium-1208`` in the
ms-playwright cache; a missing binary raises at the first visit):

    IOFAST_SMOKE_URL=https://<a-cloudflare-protected-docs-page> \
    [IOFAST_PROXY=http://user:pass@host:port] \
    engine/.venv/bin/python engine/tools/captcha_solve_smoke.py

A visible browser opens; solve the challenge by hand within the timeout. The
tool then continues headless and prints PASS iff the continue is a 200 with no
challenge markers. Pick a URL that actually throws an interstitial — a site
behind Cloudflare that does NOT challenge your IP proves nothing.
"""
import datetime
import json
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from iofast_engine.solve import solve_and_continue  # noqa: E402
from iofast_engine.tiers import is_challenge  # noqa: E402

RESULT_FILE = os.path.join(os.path.dirname(__file__), "captcha_smoke_result.json")


def _record(verdict, url, extra):
    """Write the live-gate state to a file so the next agent reads a fact, not
    tribal memory ("has this ever actually cleared Cloudflare?"). Host only —
    never the full URL — so a private docs path is not persisted.
    """
    row = {
        "verdict": verdict,  # pass | fail | inconclusive
        "url_host": urlparse(url).netloc if url else None,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        **extra,
    }
    try:
        with open(RESULT_FILE, "w", encoding="utf-8") as fh:
            json.dump(row, fh, indent=2)
        print(f"[smoke] wrote {RESULT_FILE}", flush=True)
    except OSError as exc:
        print(f"[smoke] could not write result file: {exc}", file=sys.stderr)


def _log(event):
    print(f"[smoke] {event.get('type')}: {event.get('message', '')}", flush=True)


def main():
    url = os.environ.get("IOFAST_SMOKE_URL")
    if not url:
        print("[smoke] set IOFAST_SMOKE_URL to a Cloudflare-challenged page", file=sys.stderr)
        return 2
    proxy = (os.environ.get("IOFAST_PROXY") or "").split(",")[0].strip() or None
    timeout = int(os.environ.get("IOFAST_SMOKE_TIMEOUT", "180"))
    print(f"[smoke] url={url}  proxy={'pinned' if proxy else 'none'}  timeout={timeout}s", flush=True)

    resp = solve_and_continue(url, proxy=proxy, timeout=timeout, emit=_log)
    if resp is None:
        print("\n[smoke] RESULT: INCONCLUSIVE — no continue response (unsolved/timeout)")
        _record("inconclusive", url, {"proxy_pinned": bool(proxy)})
        return 2

    body = resp.body.decode("utf-8", "ignore") if isinstance(resp.body, bytes) else str(resp.body)
    challenged = is_challenge(resp.status, dict(resp.headers or {}), body)
    ok = resp.status == 200 and not challenged
    print(f"\n[smoke] continue status={resp.status}  still_challenged={challenged}")
    print(f"[smoke] RESULT: {'PASS — live Cloudflare accepted the replay' if ok else 'FAIL — re-challenged or non-200'}")
    _record(
        "pass" if ok else "fail",
        url,
        {"status": resp.status, "still_challenged": challenged, "proxy_pinned": bool(proxy)},
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
