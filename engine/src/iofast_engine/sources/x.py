"""X (Twitter) source: post/thread/profile capture via signed-in browser DOM.

Hard rules (Eric's decision): no private APIs, no stealth patches, no CAPTCHA
solving, no automated sign-in. We read only what the rendered page shows a
signed-in user. Polite pacing: human-speed waits, hard default limit of 20
profile items (max 100), stop at first wall/challenge/rate-limit and report it.

Both x.com and twitter.com are accepted; normalized to x.com in output.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Callable

from .base import GrabOptions, GrabResult
from .social_browser import SocialWallError, close_context, open_page

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
            return await self._grab_profile(url, handle, limit, out_dir, emit)

        raise ValueError(f"unsupported x url: {url}")

    # ---- single post / thread ----

    async def _grab_post(self, url: str, status_id: str, out_dir: Path, emit) -> GrabResult:
        page = await open_page(url, emit=emit)
        try:
            await page.wait_for_timeout(2500)
            data = await self._extract_thread(page, url, status_id)
        finally:
            await close_context(page)

        md = self._render_thread_md(data)
        fname = f"thread-{status_id}.md"
        from ..writer import envelope

        (out_dir / fname).write_text(envelope(url, md), encoding="utf-8")
        emit({"type": "page", "url": url, "file": fname, "chars": len(md)})
        return GrabResult(source="x", run_dir=out_dir, ok=1, thin=0, failed=0)

    async def _extract_thread(self, page, url: str, focus_id: str) -> dict:
        """Extract the focused post plus any same-author conversation around it."""
        result: dict = {
            "focus_url": url,
            "focus_id": focus_id,
            "posts": [],
        }

        # Each tweet lives in an article[data-testid="tweet"].
        articles = await page.query_selector_all('article[data-testid="tweet"]')
        for art in articles:
            post = await self._extract_single_post(art)
            if post:
                result["posts"].append(post)

        # If extraction yielded nothing, fall back to the page text so we
        # never silently produce an empty file.
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

        # Mark the focus post (the one whose id matches the URL).
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

        # Author handle: the first link with role="link" pointing to a profile.
        try:
            links = await article.query_selector_all('a[role="link"]')
            for lnk in links:
                href = await lnk.get_attribute("href") or ""
                if href.startswith("/") and "/status/" not in href:
                    post["author"] = href.lstrip("/").split("/")[0]
                    break
        except Exception:  # noqa: BLE001
            pass

        # Tweet text.
        try:
            text_el = await article.query_selector('[data-testid="tweetText"]')
            if text_el:
                post["text"] = (await text_el.inner_text()).strip()
        except Exception:  # noqa: BLE001
            pass

        # Date.
        try:
            time_el = await article.query_selector("time[datetime]")
            if time_el:
                post["date"] = await time_el.get_attribute("datetime") or ""
        except Exception:  # noqa: BLE001
            pass

        # Post ID from the permalink.
        try:
            perma = await article.query_selector('a[href*="/status/"]')
            if perma:
                href = await perma.get_attribute("href") or ""
                m = re.search(r"/status/(\d+)", href)
                if m:
                    post["id"] = m.group(1)
        except Exception:  # noqa: BLE001
            pass

        # Media: images and videos.
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

        # Quoted tweet (nested card). Extract BEFORE we potentially filter
        # the post out, but scope all queries inside the card element so
        # nested tweetText/links don't bleed into the parent article.
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

        # Re-extract the direct tweet text scoped to the article but NOT
        # inside a nested card, so quoted tweets don't overwrite the parent.
        # Only overwrite if we didn't already get text from the first pass.
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

    # ---- profile ----

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
        # Follower/following counts are in nav links.
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
        """Scroll the profile timeline and collect up to `limit` post stubs."""
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
                # Build a permalink when we have the id.
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