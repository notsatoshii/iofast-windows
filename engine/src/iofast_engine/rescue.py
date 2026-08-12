"""Tier 3: LLM selector rescue — one call per domain, cached, BYO key.

OpenRouter-first per PLAN's decision log (BYO key, provider gateway). Both env
vars are required to arm the tier: OPENROUTER_API_KEY and IOFAST_RESCUE_MODEL
(model ids are the user's choice — none is hardcoded here). Keys travel in the
Authorization header only, never argv. Every rescue is cached per-domain in
adapters-cache.json and logged as a candidate permanent adapter.
"""

import json
import os
import time
import urllib.request
from urllib.parse import urlparse

# IOFAST_OPENROUTER_URL is a test seam: packaging/verify_frozen.py points the
# frozen binary at a local fixture server to exercise `push` end to end. It is
# read once at import, from the environment the parent set — the same trust
# boundary as IOFAST_PROXY (a parent that can set env can already redirect
# traffic wholesale).
OPENROUTER_URL = os.environ.get(
    "IOFAST_OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"
)
SAMPLE_CAP = 30_000  # chars of page HTML sent to the model
PROMPT = (
    "You are given the HTML of one documentation page. Reply with ONLY a JSON "
    'object: {"content_selector": "<css>", "nav_selector": "<css or null>"}. '
    "content_selector must select the main documentation content of pages on "
    "this site; nav_selector the sidebar/table-of-contents container, or null. "
    "No prose, no code fences.\n\nURL: {url}\n\nHTML:\n{html}"
)


def cache_path() -> str:
    root = os.environ.get("IOFAST_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".iofast"
    )
    return os.path.join(root, "adapters-cache.json")


def _load_cache() -> dict:
    try:
        with open(cache_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    path = cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=2)


def _valid_selector(selector) -> bool:
    if not selector or not isinstance(selector, str):
        return False
    try:
        from lxml.cssselect import CSSSelector

        CSSSelector(selector)
        return True
    except Exception:
        return False


def _call_llm(api_key: str, model: str, url: str, html: str, timeout: int = 60) -> str:
    body = json.dumps(
        {
            "model": model,
            "max_tokens": 500,
            "messages": [
                {
                    "role": "user",
                    "content": PROMPT.replace("{url}", url).replace(
                        "{html}", html[:SAMPLE_CAP]
                    ),
                }
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    return payload["choices"][0]["message"]["content"]


def rescue_selectors(sample_url: str, sample_html: str, emit=None) -> dict | None:
    """Selectors for this domain, from cache or one LLM call. None = tier unavailable/failed."""
    emit = emit or (lambda event: None)
    domain = urlparse(sample_url).netloc.lower()

    cache = _load_cache()
    if domain in cache:
        hit = cache[domain]
        emit(
            {
                "type": "rescue",
                "domain": domain,
                "cached": True,
                "message": f"🛟 rescue cache hit for {domain}: {hit['content_selector']}",
            }
        )
        return hit

    api_key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("IOFAST_RESCUE_MODEL")
    if not api_key or not model:
        emit(
            {
                "type": "warning",
                "message": (
                    "🛟 rescue tier unavailable — set OPENROUTER_API_KEY and "
                    "IOFAST_RESCUE_MODEL to enable the LLM selector rescue"
                ),
            }
        )
        return None

    try:
        text = _call_llm(api_key, model, sample_url, sample_html)
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        parsed = json.loads(text)
    except Exception as error:
        emit(
            {
                "type": "warning",
                "message": f"🛟 rescue call failed: {type(error).__name__}: {error}",
            }
        )
        return None

    content_selector = parsed.get("content_selector")
    nav_selector = parsed.get("nav_selector")
    if not _valid_selector(content_selector):
        emit(
            {
                "type": "warning",
                "message": f"🛟 rescue returned an unusable selector: {content_selector!r}",
            }
        )
        return None

    entry = {
        "content_selector": content_selector,
        "nav_selector": nav_selector if _valid_selector(nav_selector) else None,
        "model": model,
        "sample_url": sample_url,
        "rescued_at": time.strftime("%Y-%m-%d"),
    }
    cache[domain] = entry
    _save_cache(cache)
    emit(
        {
            "type": "rescue",
            "domain": domain,
            "cached": False,
            "selectors": entry,
            "message": (
                f"🛟 rescued {domain}: content={content_selector} — "
                "logged as a candidate permanent adapter in adapters-cache.json"
            ),
        }
    )
    return entry
