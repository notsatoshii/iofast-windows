"""X (Twitter) source: public-first capture with signed-in browser fallback.

Order per capture:
1. Syndication API (cdn.syndication.twimg.com/tweet-result) — what the embed widget calls.
2. oEmbed (publish.twitter.com/oembed) as fallback.
3. Signed-in browser route if a profile exists; otherwise clear error.

Hard rules: public content only on the public route, handful of requests per
capture, polite back-off on 429/5xx, never retry-hammer. No proxy rotation,
no fingerprint spoofing, no CAPTCHA solving, no automated login.

Both x.com and twitter.com are accepted; normalized to x.com in output.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .base import GrabOptions, GrabResult
from .social_browser import SocialWallError, close_context, open_page
from .social_public import (
    PublicResult,
    download_media,
    maybe_transcribe,
    x_fetch_oembed,
    x_fetch_syndication,
)

DEFAULT_PROFILE_LIMIT = 20
MAX_PROFILE_LIMIT = 100


class XSource:
    name = "x"

    # Matches x.com or twitter.com status/<id> or <user>/status/<id>.
    _RE_POST = re.compile(
        r"https?://(?:www\.)?(?:x|twitter)\.com/(?:[A-Za-z0-9_]+/)?status/(\d+)"
    )
    # Matches a bare profile URL.
    _RE_PROFILE = re.compile(
        r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]+)/?(?:\?.*)?$"
    )

    def matches(self, url: str) -> bool:
        return bool(self._RE_POST.match(url) or self._RE_PROFILE.match(url))

    async def grab(
        self,
        url: str,
        out_root: Path,
        opts: GrabOptions,
        emit: Callable[[dict], None],
    ) -> GrabResult:
        out_dir = out_root / "x"
        out_dir.mkdir(parents=True, exist_ok=True)

        post_match = self._RE_POST.match(url)
        if post_match:
            return await self._grab_post(url, post_match.group(1), out_dir, emit)

        profile_match = self._RE_PROFILE.match(url)
        if profile_match:
            handle = profile_match.group(1)
            reserved = {"home", "explore", "notifications", "messages", "settings", "i", "search"}
            if handle.lower() in reserved:
                raise ValueError(f"not an x profile: {url}")
            limit = min(opts.limit or DEFAULT_PROFILE_LIMIT, MAX_PROFILE_LIMIT)
            # Profiles always need the signed-in route (no public profile API).
            return await self._grab_profile(url, handle, limit, out_dir, emit)

        raise ValueError(f"unsupported x url: {url}")

    # ---- single post / thread ----

    async def _grab_post(self, url: str, status_id: str, out_dir: Path, emit) -> GrabResult:
        """Try public endpoints first, fall back to signed-in browser."""
        route = "public"
        data: dict | None = None

        # Route 1: syndication API.
        result = await x_fetch_syndication(status_id)
        if result.ok:
            data = result.data
            emit({"type": "info", "url": url, "message": "captured via public syndication"})
        else:
            emit({"type": "info", "url": url, "message": f"syndication failed ({result.error}); trying oembed"})

        # Route 2: oEmbed fallback.
        if data is None:
            result = await x_fetch_oembed(url)
            if result.ok:
                data = result.data
                emit({"type": "info", "url": url, "message": "captured via public oembed"})
            else:
                emit({"type": "info", "url": url, "message": f"oembed failed ({result.error}); trying signed-in browser"})

        # Route 3: signed-in browser fallback.
        if data is None:
            route = "signed-in"
            try:
                data = await self._grab_post_signed_in(url, status_id, emit)
            except SocialWallError as exc:
                emit({"type": "error", "url": url, "message": f"signed-in route hit wall: {exc}"})
                from ..writer import envelope
                err_md = (
                    f"# X capture failed\n\n**URL:** {url}\n**Status ID:** {status_id}\n\n"
                    f"Sign-in is required for this item but the browser profile is not "
                    f"authenticated or hit a wall.\n\nError: {exc}\n"
                )
                fname = f"thread-{status_id}.md"
                (out_dir / fname).write_text(envelope(url, err_md), encoding="utf-8")
                return GrabResult(source="x", run_dir=out_dir, ok=0, thin=0, failed=1)

        # Download video media for transcription.
        transcript_note = ""
        for m in data.get("media", []):
            if m.get("type") == "video" and m.get("url"):
                vid_name = f"{status_id}-video.mp4"
                vid_path = out_dir / vid_name
                ok, info = await download_media(m["url"], vid_path)
                if ok:
                    emit({"type": "media", "url": m["url"], "file": vid_name, "size": info})
                    tx = maybe_transcribe(vid_path, emit=emit)
                    if tx == "no audio stream in file" and m.get("audio_url"):
                        # Video-only stream: transcribe the separate audio track.
                        aud_path = vid_path.with_name(vid_path.stem + "-audio.m4a")
                        a_ok, a_info = await download_media(m["audio_url"], aud_path)
                        if a_ok:
                            emit({"type": "media", "url": m["audio_url"], "file": aud_path.name, "size": a_info})
                            tx = maybe_transcribe(aud_path, emit=emit)
                    if tx:
                        # Distinguish success (has header) from error strings.
                        if tx.startswith("Transcript:"):
                            data.setdefault("transcript", tx)
                        else:
                            transcript_note = tx
                            emit({"type": "warning", "url": url, "message": f"transcription: {tx}"})
                    else:
                        transcript_note = "transcription not installed"
                        emit({"type": "warning", "url": url, "message": "transcription: not installed"})
                else:
                    emit({"type": "warning", "url": m["url"], "message": f"download failed: {info}"})

        md = self._render_post_md(data, route=route, transcript_note=transcript_note)
        fname = f"thread-{status_id}.md"
        from ..writer import envelope

        (out_dir / fname).write_text(envelope(url, md), encoding="utf-8")
        emit({"type": "page", "url": url, "file": fname, "chars": len(md), "route": route})
        return GrabResult(source="x", run_dir=out_dir, ok=1, thin=0, failed=0)

    async def _grab_post_signed_in(self, url: str, status_id: str, emit) -> dict:
        """Signed-in browser DOM extraction (original route)."""
        page = await open_page(url, emit=emit)
        try:
            await page.wait_for_timeout(2500)
            thread_data = await self._extract_thread(page, url, status_id)
        finally:
            await close_context(page)

        # Flatten thread_data into our normalized shape. The public route returns
        # a single-post dict; the signed-in route may return multiple posts.
        # Use the focused post as the primary, attach others as context.
        focused = None
        for p in thread_data.get("posts", []):
            if p.get("is_focus"):
                focused = p
                break
        if not focused and thread_data.get("posts"):
            focused = thread_data["posts"][0]
        if not focused:
            focused = {"author": "", "text": "", "date": "", "media": [], "quoted": None}

        result = {
            "id": status_id,
            "author": focused.get("author", ""),
            "handle": focused.get("author", ""),
            "text": focused.get("text", ""),
            "date": focused.get("date", ""),
            "media": focused.get("media", []),
            "quoted": focused.get("quoted"),
            "reply_to": "",
            "reply_to_id": "",
            "likes": None,
            "retweets": None,
            "replies": None,
            "quotes": None,
            "canonical_url": url,
        }

        # Attach thread context if there are additional posts.
        other_posts = [p for p in thread_data.get("posts", []) if not p.get("is_focus")]
        if other_posts:
            result["thread_context"] = other_posts

        return result

    async def _extract_thread(self, page, url: str, focus_id: str) -> dict:
        """Extract the focused post plus any same-author conversation around it."""
        result: dict = {
            "focus_url": url,
            "focus_id": focus_id,
            "posts": [],
        }

        articles = await page.query_selector_all('article[data-testid="tweet"]')
        for art in articles:
            post = await self._extract_single_post(art)
            if post:
                result["posts"].append(post)

        if not result["posts"]:
            try:
                body = (await page.inner_text("main"))[:6000]
            except Exception:  # noqa: BLE001
                body = ""
            result["posts"].append({
                "author": "",
                "text": body.strip(),
                "date": "",
                "media": [],
                "quoted": None,
                "id": focus_id,
                "is_focus": True,
            })

        for p in result["posts"]:
            p["is_focus"] = p.get("id") == focus_id
        return result

    async def _extract_single_post(self, article) -> dict | None:
        """Pull structured data from one tweet article element."""
        post: dict = {
            "author": "",
            "text": "",
            "date": "",
            "media": [],
            "quoted": None,
            "id": "",
            "is_focus": False,
        }

        try:
            links = await article.query_selector_all('a[role="link"]')
            for lnk in links:
                href = await lnk.get_attribute("href") or ""
                if href.startswith("/") and "/status/" not in href:
                    post["author"] = href.lstrip("/").split("/")[0]
                    break
        except Exception:  # noqa: BLE001
            pass

        try:
            text_el = await article.query_selector('[data-testid="tweetText"]')
            if text_el:
                post["text"] = (await text_el.inner_text()).strip()
        except Exception:  # noqa: BLE001
            pass

        try:
            time_el = await article.query_selector("time[datetime]")
            if time_el:
                post["date"] = await time_el.get_attribute("datetime") or ""
        except Exception:  # noqa: BLE001
            pass

        try:
            perma = await article.query_selector('a[href*="/status/"]')
            if perma:
                href = await perma.get_attribute("href") or ""
                m = re.search(r"/status/(\d+)", href)
                if m:
                    post["id"] = m.group(1)
        except Exception:  # noqa: BLE001
            pass

        try:
            imgs = await article.query_selector_all('img[src*="media"]')
            for img in imgs[:4]:
                src = await img.get_attribute("src") or ""
                alt = await img.get_attribute("alt") or ""
                if src:
                    post["media"].append({"type": "image", "url": src.split("?")[0], "alt": alt})
        except Exception:  # noqa: BLE001
            pass
        try:
            vids = await article.query_selector_all("video source")
            for vid in vids[:2]:
                src = await vid.get_attribute("src") or ""
                if src:
                    post["media"].append({"type": "video", "url": src, "alt": ""})
        except Exception:  # noqa: BLE001
            pass

        try:
            card = await article.query_selector('[data-testid="card.wrapper"]')
            if card:
                qt_text_el = await card.query_selector('[data-testid="tweetText"]')
                qt_text = (await qt_text_el.inner_text()).strip() if qt_text_el else ""
                qt_author = ""
                qt_links = await card.query_selector_all('a[role="link"]')
                for lnk in qt_links:
                    href = await lnk.get_attribute("href") or ""
                    if href.startswith("/") and "/status/" not in href:
                        qt_author = href.lstrip("/").split("/")[0]
                        break
                post["quoted"] = {"author": qt_author, "text": qt_text}
        except Exception:  # noqa: BLE001
            pass

        if not post["text"]:
            try:
                direct_texts = await article.query_selector_all('[data-testid="tweetText"]')
                for candidate in direct_texts:
                    parent_card = await candidate.query_selector(
                        'ancestor::div[@data-testid="card.wrapper"]'
                    )
                    if parent_card is None:
                        post["text"] = (await candidate.inner_text()).strip()
                        break
            except Exception:  # noqa: BLE001
                pass

        if not post["text"] and not post["media"]:
            return None
        return post

    @staticmethod
    def _render_post_md(data: dict, *, route: str = "public", transcript_note: str = "") -> str:
        author = data.get("author") or data.get("handle") or "unknown"
        handle = data.get("handle") or author
        lines = [f"# Post by @{handle}", ""]
        lines.append(f"**Route:** {route}")
        if data.get("date"):
            lines.append(f"**Date:** {data['date']}")
        if data.get("likes") is not None:
            lines.append(f"**Likes:** {data['likes']:,}")
        if data.get("retweets") is not None:
            lines.append(f"**Retweets:** {data['retweets']:,}")
        if data.get("replies") is not None:
            lines.append(f"**Replies:** {data['replies']:,}")
        if data.get("reply_to"):
            lines.append(f"**In reply to:** @{data['reply_to']}")
        lines.append("")

        if data.get("text"):
            lines.extend(["## Text", "", data["text"], ""])

        if data.get("media"):
            lines.append("## Media")
            lines.append("")
            for m in data["media"]:
                if m["type"] == "image":
                    alt = m.get("alt", "")
                    lines.append(f"![{alt}]({m['url']})")
                else:
                    lines.append(f"[Video]({m['url']})")
            lines.append("")

        if data.get("quoted"):
            q = data["quoted"]
            qa = f"@{q['author']}" if q.get("author") else "(unknown)"
            lines.append(f"> **Quoted {qa}:**")
            for ql in q.get("text", "").splitlines():
                lines.append(f"> {ql}")
            lines.append("")

        if data.get("transcript"):
            lines.extend(["## Transcript", "", data["transcript"], ""])
        elif transcript_note:
            lines.append(f"*({transcript_note})*")
            lines.append("")

        # Thread context from signed-in route.
        ctx = data.get("thread_context") or []
        if ctx:
            lines.append("## Thread context")
            lines.append("")
            for i, p in enumerate(ctx):
                pa = f"@{p['author']}" if p.get("author") else "(unknown)"
                lines.append(f"### {pa}")
                if p.get("date"):
                    lines.append(f"**Date:** {p['date']}")
                lines.append("")
                if p.get("text"):
                    lines.append(p["text"])
                    lines.append("")
                if p.get("media"):
                    for m in p["media"]:
                        if m["type"] == "image":
                            lines.append(f"![{m.get('alt', '')}]({m['url']})")
                        else:
                            lines.append(f"[Video]({m['url']})")
                    lines.append("")
                if p.get("quoted"):
                    q = p["quoted"]
                    qa = f"@{q['author']}" if q.get("author") else "(unknown)"
                    lines.append(f"> **Quoted {qa}:**")
                    for ql in q.get("text", "").splitlines():
                        lines.append(f"> {ql}")
                    lines.append("")

        return "\n".join(lines)

    # Legacy renderer kept for profile posts that still use the old shape.
    @staticmethod
    def _render_thread_md(data: dict) -> str:
        lines = [f"# X thread (focus: {data['focus_id']})", ""]
        for i, p in enumerate(data["posts"]):
            marker = " **(focused)**" if p.get("is_focus") else ""
            author = f"@{p['author']}" if p.get("author") else "(unknown)"
            lines.append(f"## Post {i + 1} by {author}{marker}")
            if p.get("date"):
                lines.append(f"**Date:** {p['date']}")
            lines.append("")
            if p.get("text"):
                lines.append(p["text"])
                lines.append("")
            if p.get("media"):
                for m in p["media"]:
                    if m["type"] == "image":
                        lines.append(f"![{m.get('alt', '')}]({m['url']})")
                    else:
                        lines.append(f"[Video]({m['url']})")
                lines.append("")
            if p.get("quoted"):
                q = p["quoted"]
                qa = f"@{q['author']}" if q.get("author") else "(unknown)"
                lines.append(f"> **Quoted {qa}:**")
                for ql in q.get("text", "").splitlines():
                    lines.append(f"> {ql}")
                lines.append("")
        return "\n".join(lines)

    # ---- profile (always signed-in) ----

    async def _grab_profile(
        self, url: str, handle: str, limit: int, out_dir: Path, emit
    ) -> GrabResult:
        page = await open_page(url, emit=emit)
        try:
            await page.wait_for_timeout(2500)
            profile_data = await self._extract_profile(page, handle)
            posts = await self._scroll_posts(page, limit, emit)
        finally:
            await close_context(page)

        from ..writer import envelope

        ok = 0
        for i, post in enumerate(posts):
            fname = f"{handle}-post-{i + 1}.md"
            md = self._render_single_post_md(post, handle)
            (out_dir / fname).write_text(envelope(post.get("url", url), md), encoding="utf-8")
            ok += 1
            emit({"type": "page", "url": post.get("url", url), "file": fname, "chars": len(md)})

        index_md = self._render_profile_index(profile_data, posts, handle)
        idx_name = f"{handle}-index.md"
        (out_dir / idx_name).write_text(envelope(url, index_md), encoding="utf-8")
        emit({"type": "page", "url": url, "file": idx_name, "chars": len(index_md)})

        return GrabResult(source="x", run_dir=out_dir, ok=ok + 1, thin=0, failed=0)

    async def _extract_profile(self, page, handle: str) -> dict:
        data: dict = {
            "handle": handle,
            "bio": "",
            "posts": None,
            "followers": None,
            "following": None,
        }
        try:
            bio_el = await page.query_selector('[data-testid="UserDescription"]')
            if bio_el:
                data["bio"] = (await bio_el.inner_text()).strip()
        except Exception:  # noqa: BLE001
            pass
        try:
            links = await page.query_selector_all('a[role="link"]')
            for lnk in links:
                href = await lnk.get_attribute("href") or ""
                txt = (await lnk.inner_text()).strip()
                if f"/{handle}/followers" in href:
                    data["followers"] = txt.split("\n")[0].strip()
                elif f"/{handle}/following" in href:
                    data["following"] = txt.split("\n")[0].strip()
        except Exception:  # noqa: BLE001
            pass
        return data

    async def _scroll_posts(self, page, limit: int, emit) -> list[dict]:
        seen: set[str] = set()
        posts: list[dict] = []
        stale_scrolls = 0

        while len(posts) < limit and stale_scrolls < 2:
            articles = await page.query_selector_all('article[data-testid="tweet"]')
            new_count = 0
            for art in articles:
                post = await self._extract_single_post(art)
                if not post:
                    continue
                key = post.get("id") or post.get("text", "")[:80]
                if key in seen:
                    continue
                seen.add(key)
                if post.get("id") and post.get("author"):
                    post["url"] = f"https://x.com/{post['author']}/status/{post['id']}"
                posts.append(post)
                new_count += 1
                if len(posts) >= limit:
                    break

            if new_count == 0:
                stale_scrolls += 1
            else:
                stale_scrolls = 0

            await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
            await page.wait_for_timeout(1500)

        return posts

    @staticmethod
    def _render_single_post_md(post: dict, handle: str) -> str:
        author = post.get("author") or handle
        lines = [f"# Post by @{author}", ""]
        if post.get("date"):
            lines.append(f"**Date:** {post['date']}")
            lines.append("")
        if post.get("text"):
            lines.append(post["text"])
            lines.append("")
        if post.get("media"):
            for m in post["media"]:
                if m["type"] == "image":
                    lines.append(f"![{m.get('alt', '')}]({m['url']})")
                else:
                    lines.append(f"[Video]({m['url']})")
            lines.append("")
        if post.get("quoted"):
            q = post["quoted"]
            qa = f"@{q.get('author', '')}" if q.get("author") else "(unknown)"
            lines.append(f"> **Quoted {qa}:**")
            for ql in q.get("text", "").splitlines():
                lines.append(f"> {ql}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_profile_index(profile_data: dict, posts: list[dict], handle: str) -> str:
        lines = [f"# X profile: @{handle}", ""]
        if profile_data.get("bio"):
            lines.extend(["## Bio", "", profile_data["bio"], ""])
        counts = []
        for key in ("posts", "followers", "following"):
            if profile_data.get(key):
                counts.append(f"**{key.title()}:** {profile_data[key]}")
        if counts:
            lines.extend(counts)
        lines.append("")
        lines.append(f"## Latest {len(posts)} posts")
        lines.append("")
        for i, post in enumerate(posts):
            text = post.get("text", "")[:80]
            label = text or "(no text)"
            url = post.get("url", "")
            lines.append(f"{i + 1}. [{label}]({url})")
        lines.append("")
        return "\n".join(lines)