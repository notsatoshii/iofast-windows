"""RPC handlers for social.signin and social.status (docs/RPC.md).

Plain functions taking (params, emit) and returning the result dict. Another
branch owns rpc.py and wires these in at merge time; do not edit rpc.py here.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable


def _run_async(coro):
    """Run an async coroutine from synchronous RPC dispatch code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already inside an async context (shouldn't happen for RPC dispatch,
        # but be safe): create a task and run to completion.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    return asyncio.run(coro)


def signin(params: dict[str, Any], emit: Callable[[dict], None]) -> dict:
    """social.signin {site: "instagram"|"x"} -> {signed_in: bool}.

    Opens a visible browser on the site's login page using the dedicated
    persistent profile. Resolves when the user closes the window.
    """
    site = params.get("site")
    if site not in ("instagram", "x"):
        raise ValueError(f"social.signin: site must be 'instagram' or 'x', got {site!r}")

    from .social_browser import signin as _signin

    emit({"type": "progress", "message": f"opening {site} sign-in window (close when done)"})
    return _run_async(_signin(site))


def status(params: dict[str, Any], emit: Callable[[dict], None]) -> dict:
    """social.status {} -> {instagram: bool, x: bool}.

    Reports signed-in state per site without opening a visible window.
    """
    from .social_browser import status as _status

    return _run_async(_status())