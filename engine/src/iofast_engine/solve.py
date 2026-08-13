"""Interactive CAPTCHA handoff (PLAN Phase 4, slice #5).

When the stealth tier's auto-solver cannot clear a Cloudflare challenge, this
opens a VISIBLE browser so the user solves it once by hand, then continues the
fetch headless — reusing the clearance the solve produced.

The mechanism (proven for the shell by tools/captcha_continuity_spike.py): a
Cloudflare clearance cookie lands in a persistent ``user_data_dir`` during the
headed solve and is replayed by a later headless fetch on that SAME profile,
across separate browser processes. cf_clearance binds the UA and the egress IP,
so the two sessions MUST present one identical UA and one non-rotating proxy —
all three (shared profile, pinned UA, single proxy) live here, self-contained,
so the async stealth tier that detected the challenge needs no change.

``solve_and_continue`` is SYNCHRONOUS (sync playwright). The caller runs it off
the asyncio loop (``run_in_executor``) — sync playwright raises inside a running
loop; a plain executor thread has none.

NOT proven against live Cloudflare (the manual smoke owed before this ships
headed): real cf_clearance acceptance, headed→headless fingerprint parity,
IP stability, and the real cookie's Secure+HttpOnly+SameSite attributes. This
local-mechanism code is buildable, not yet a live pass.
"""
import shutil
import tempfile
import time

from .tiers import is_challenge


def _human_solve(page, timeout_s, cancel_event, captured):
    """page_action for the HEADED session: hold the visible page open while the
    human solves, capture the real build UA, and poll until the Cloudflare
    markers clear. Sets captured["solved"] on success; leaves it False on
    timeout or cancel so the caller reports the handoff failed.
    """
    try:
        captured["ua"] = page.evaluate("() => navigator.userAgent") or ""
    except Exception:
        captured["ua"] = ""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            return
        try:
            html = page.content()
        except Exception:
            html = ""
        # Body markers only — no cf-mitigated header is visible from the page.
        if html and not is_challenge(200, {}, html):
            captured["solved"] = True
            return
        page.wait_for_timeout(1000)  # playwright's own sleep; 1s between checks


def solve_and_continue(url, *, proxy=None, timeout=180, cancel_event=None, emit=None):
    """Open a visible browser for a one-time human solve, then continue headless.

    Returns the headless continue's scrapling ``Response`` on success, or None if
    the user did not solve in time (or cancelled). ``timeout`` is the human-solve
    budget in seconds. ``proxy`` is a SINGLE proxy string (or None) — never a
    rotator: cf_clearance is IP-bound.
    """
    emit = emit or (lambda event: None)
    from scrapling.fetchers import StealthySession

    profile = tempfile.mkdtemp(prefix="iofast-solve-")
    captured = {"ua": "", "solved": False}
    proxy_kw = {"proxy": proxy} if proxy else {}
    emit(
        {
            "type": "human_required",
            "url": url,
            "timeout": timeout,
            "message": (
                f"🧩 solve the challenge in the browser window to continue: {url} "
                f"(waiting up to {timeout}s)"
            ),
        }
    )
    try:
        with StealthySession(
            headless=False,
            user_data_dir=profile,
            solve_cloudflare=False,  # the auto-solver already failed; the human solves
            max_pages=1,
            **proxy_kw,
        ) as headed:
            headed.fetch(
                url,
                timeout=timeout * 1000 + 60000,  # human budget + headroom so a late solve doesn't race the fetch timeout
                page_action=lambda page: _human_solve(page, timeout, cancel_event, captured),
            )
        if not captured["solved"]:
            emit(
                {
                    "type": "warning",
                    "url": url,
                    "message": f"⚠️  challenge not solved in time: {url}",
                }
            )
            return None
        # Continue headless on the SAME profile, pinning the headed session's real
        # build UA (session-construction arg — there is no per-fetch UA override).
        ua_kw = {"useragent": captured["ua"]} if captured["ua"] else {}
        with StealthySession(
            headless=True,
            user_data_dir=profile,
            solve_cloudflare=False,  # clearance already carries in the shared profile
            max_pages=1,
            **ua_kw,
            **proxy_kw,
        ) as headless:
            return headless.fetch(url)
    finally:
        # Paired with human_required on EVERY exit — solved, timed out, cancelled,
        # or raised. A UI banner announcing the visible window has no other signal
        # that the wait is over: the crawl runs other pages concurrently, so their
        # events keep arriving throughout the solve and cannot mark its end.
        emit({"type": "human_done", "url": url, "solved": captured["solved"]})
        shutil.rmtree(profile, ignore_errors=True)
