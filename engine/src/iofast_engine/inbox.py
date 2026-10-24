"""Phone inbox client: links sent from the phone, picked up by the desktop app.

Server contract (agentos.rest/iofast-inbox):
  GET  /links             -> 200 {"links": [{"id", "url", "source", "captured_at"}]}
                             pending only, oldest first; 401 on a bad key
  POST /links/{id}/done   JSON {"ok": bool, "note": str} -> 200, drops it from pending
  GET  /health            -> 200 {"ok": true}, no auth

The shell (Rust) owns the loop, the grab and the toast; this module is only
the HTTP half, so it can be tested against a local fake server. The key arrives
per call in the RPC request body and is never logged or put in an error text.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://agentos.rest/iofast-inbox"
TIMEOUT = 15  # seconds; one poll, not a crawl
NOTE_MAX = 300  # the server wants a short note, not a traceback
USER_AGENT = "iofast-desktop-inbox"


class InboxAuthError(Exception):
    """401: the stored phone inbox key was rejected. Not retryable until fixed."""


class InboxNetError(Exception):
    """Network or server failure. Links stay pending; the next poll retries."""


def _base(base):
    base = (base or DEFAULT_BASE).rstrip("/")
    parts = urllib.parse.urlsplit(base)
    local = parts.hostname in ("127.0.0.1", "localhost", "::1")
    # The key rides in a header: never send it in clear text off this machine.
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ValueError("inbox base must be https (http only for localhost)")
    return base


def _call(method, url, key, body=None, timeout=TIMEOUT):
    data = None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise InboxAuthError("phone inbox key was rejected (401)") from None
        raise InboxNetError(f"inbox server answered {error.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        reason = getattr(error, "reason", error)
        raise InboxNetError(f"inbox unreachable: {type(reason).__name__}") from None
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise InboxNetError("inbox server sent invalid JSON") from None


def fetch_links(key, base=None, timeout=TIMEOUT):
    """Pending links, oldest first. Rows without a string id + http(s) url are dropped."""
    if not isinstance(key, str) or not key.strip():
        raise InboxAuthError("no phone inbox key")
    payload = _call("GET", f"{_base(base)}/links", key.strip(), timeout=timeout)
    rows = payload.get("links") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise InboxNetError("inbox server sent no links list")
    links = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        link_id = row.get("id")
        url = row.get("url")
        if isinstance(link_id, int) and not isinstance(link_id, bool):
            link_id = str(link_id)
        if not isinstance(link_id, str) or not link_id or not isinstance(url, str):
            continue
        links.append(
            {
                "id": link_id,
                "url": url.strip(),
                "source": row.get("source") if isinstance(row.get("source"), str) else "",
                "captured_at": row.get("captured_at"),
            }
        )
    return links


def short_note(note):
    note = " ".join(str(note or "").split())
    return note if len(note) <= NOTE_MAX else note[: NOTE_MAX - 1] + "…"


def mark_done(key, link_id, ok, note, base=None, timeout=TIMEOUT):
    """Tell the server this link is handled (saved or failed for good)."""
    if not isinstance(key, str) or not key.strip():
        raise InboxAuthError("no phone inbox key")
    quoted = urllib.parse.quote(str(link_id), safe="")
    _call(
        "POST",
        f"{_base(base)}/links/{quoted}/done",
        key.strip(),
        body={"ok": bool(ok), "note": short_note(note)},
        timeout=timeout,
    )
    return {"ok": True}


def health(base=None, timeout=TIMEOUT):
    payload = _call("GET", f"{_base(base)}/health", None, timeout=timeout)
    return isinstance(payload, dict) and payload.get("ok") is True
