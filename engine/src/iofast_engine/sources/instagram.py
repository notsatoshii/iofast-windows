"""Instagram source: post/reel/profile capture via signed-in browser DOM.

Hard rules (Eric's decision): no private APIs, no stealth patches, no CAPTCHA
solving, no automated sign-in. We read only what the rendered page shows a
signed-in user. Polite pacing: human-speed waits, hard default limit of 20
profile items (max 100), stop at first wall/challenge/rate-limit and report it.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Callable

from .base import GrabOptions, GrabResult
from .social_browser import SocialWallError, close_context, open_page

# Profile captures default to 20 items; callers can raise to 100 max.
DEFAULT_PROFILE_LIMIT = 20
MAX_PROFILE_LIMIT = 100


class InstagramSource:
    name = "instagram"

    _RE_POST = re.compile(
        r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)"
    )
    _RE_PROFILE = re.compile(
        r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?(?:\?.*)?$"
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
        out_dir = out_root / "instagram"
        out_dir.mkdir(parents=True, exist_ok=True)

        post_match = self._RE_POST.match(url)
        if post_match:
            return await self._grab_post(url, post_match.group(1), out_dir, emit)

        profile_match = self._RE_PROFILE.match(url)
        if profile_match:
            username = profile_match.group(1)
            # Skip reserved paths that look like profiles but aren't.
            if username.lower() in {"accounts", "explore", "direct", "stories", "reels"}:
                raise ValueError(f"not an instagram profile: {url}")
            limit = min(opts.limit or DEFAULT_PROFILE_LIMIT, MAX_PROFILE_LIMIT)
            return await self._grab_profile(url, username, limit, out_dir, emit)

        raise ValueError(f"unsupported instagram url: {url}")

    # ---- single post / reel ----

    async def _grab_post(self, url: str, shortcode: str, out_dir: Path, emit) -> GrabResult:
        page = await open_page(url, emit=emit)
        try:
            await page.wait_for_timeout(2000)
            data = await self._extract_post(page, url, shortcode)
        finally:
            await close_context(page)

        md = self._render_post_md(data)
        path = out_dir / f"{shortcode}.md"
        from ..writer import envelope

        path.write_text(envelope(url, md), encoding="utf-8")

        emit({"type": "page", "url": url, "file": path.name, "chars": len(md)})
        return GrabResult(source="instagram", run_dir=out_dir, ok=1, thin=0, failed=0)

    async def _extract_post(self, page, url: str, shortcode: str) -> dict:
        """Pull structured data from a post/reel page's rendered DOM."""
        result: dict = {
            "url": url,
            "shortcode": shortcode,
            "author": "",
            "caption": "",
            "date": "",
            "likes": None,
            "comments_count": None,
            "media": [],
            "top_comments": [],
        }

        # Author: first link to /username/ in the header area.
        try:
            author_el = await page.query_selector('header a[href^="/"]')
            if author_el:
                href = await author_el.get_attribute("href") or ""
                result["author"] = href.strip("/")
        except Exception:  # noqa: BLE001
            pass

        # Caption: the span inside the article body that holds the post text.
        try:
            cap_el = await page.query_selector("article span[dir='auto']")
            if cap_el:
                result["caption"] = (await cap_el.inner_text()).strip()
        except Exception:  # noqa: BLE001
            pass

        # Date: time element with datetime attribute.
        try:
            time_el = await page.query_selector("time[datetime]")
            if time_el:
                result["date"] = await time_el.get_attribute("datetime") or ""
        except Exception:  # noqa: BLE001
            pass

        # Likes: section containing "likes" or "others" text.
        try:
            likes_el = await page.query_selector("section button span")
            if likes_el:
                txt = (await likes_el.inner_text()).strip()
                nums = re.findall(r"[\d,]+", txt)
                if nums:
                    result["likes"] = int(nums[0].replace(",", ""))
        except Exception:  # noqa: BLE001
            pass

        # Media: images and videos visible on the post.
        try:
            imgs = await page.query_selector_all("article img[srcset]")
            for img in imgs[:5]:
                src = await img.get_attribute("srcset") or await img.get_attribute("src") or ""
                alt = await img.get_attribute("alt") or ""
                if src:
                    result["media"].append({"type": "image", "url": src.split("?")[0], "alt": alt})
        except Exception:  # noqa: BLE001
            pass
        try:
            vids = await page.query_selector_all("article video source")
            for vid in vids[:3]:
                src = await vid.get_attribute("src") or ""
                if src:
                    result["media"].append({"type": "video", "url": src, "alt": ""})
        except Exception:  # noqa: BLE001
            pass

        # Top comments: li elements under the comments ul.
        try:
            comment_els = await page.query_selector_all("ul li div[dir='auto']")
            for el in comment_els[:10]:
                txt = (await el.inner_text()).strip()
                if txt and len(txt) > 2:
                    result["top_comments"].append(txt)
        except Exception:  # noqa: BLE001
            pass

        return result

    @staticmethod
    def _render_post_md(data: dict) -> str:
        author = data.get("author", "") or "unknown"
        lines = [f"# Instagram post by @{author}", ""]
        if data.get("date"):
            lines.append(f"**Date:** {data['date']}")
        if data.get("likes") is not None:
            lines.append(f"**Likes:** {data['likes']:,}")
        if data.get("comments_count") is not None:
            lines.append(f"**Comments:** {data['comments_count']:,}")
        lines.append("")
        if data.get("caption"):
            lines.extend(["## Caption", "", data["caption"], ""])
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
        if data.get("top_comments"):
            lines.append("## Top comments")
            lines.append("")
            for c in data["top_comments"]:
                lines.append(f"- {c}")
            lines.append("")
        return "\n".join(lines)

    # ---- profile ----

    async def _grab_profile(
        self, url: str, username: str, limit: int, out_dir: Path, emit
    ) -> GrabResult:
        page = await open_page(url, emit=emit)
        try:
            await page.wait_for_timeout(2000)
            profile_data = await self._extract_profile(page, username)
            posts = await self._scroll_posts(page, limit, emit)
        finally:
            await close_context(page)

        # Write one file per post + an index file.
        from ..writer import envelope

        ok = 0
        for i, post in enumerate(posts):
            fname = f"{username}-post-{i + 1}.md"
            # Ensure every stub has the keys _render_post_md expects.
            post.setdefault("author", username)
            post.setdefault("caption", "")
            post.setdefault("date", "")
            post.setdefault("likes", None)
            post.setdefault("comments_count", None)
            post.setdefault("media", [])
            post.setdefault("top_comments", [])
            md = self._render_post_md(post)
            (out_dir / fname).write_text(envelope(post.get("url", url), md), encoding="utf-8")
            ok += 1
            emit({"type": "page", "url": post.get("url", url), "file": fname, "chars": len(md)})

        # Index file with profile summary + links to each post.
        index_md = self._render_profile_index(profile_data, posts, username)
        idx_name = f"{username}-index.md"
        (out_dir / idx_name).write_text(envelope(url, index_md), encoding="utf-8")
        emit({"type": "page", "url": url, "file": idx_name, "chars": len(index_md)})

        return GrabResult(source="instagram", run_dir=out_dir, ok=ok + 1, thin=0, failed=0)

    async def _extract_profile(self, page, username: str) -> dict:
        data: dict = {
            "username": username,
            "bio": "",
            "posts": None,
            "followers": None,
            "following": None,
        }
        try:
            bio_el = await page.query_selector("div.-vDIg span") or await page.query_selector(
                "header span[dir='auto']"
            )
            if bio_el:
                data["bio"] = (await bio_el.inner_text()).strip()
        except Exception:  # noqa: BLE001
            pass

        # Counts are typically in header li/span elements.
        try:
            lis = await page.query_selector_all("header ul li")
            labels = ["posts", "followers", "following"]
            for i, li in enumerate(lis[:3]):
                txt = (await li.inner_text()).strip()
                nums = re.findall(r"[\d,.]+[KMB]?", txt)
                if nums and i < len(labels):
                    data[labels[i]] = txt.split("\n")[0].strip()
        except Exception:  # noqa: BLE001
            pass
        return data

    async def _scroll_posts(self, page, limit: int, emit) -> list[dict]:
        """Scroll the profile grid and collect up to `limit` post stubs.

        Human-speed pacing: ~1.5s between scrolls. Stops early if no new
        posts appear after two consecutive scrolls (end of feed).
        """
        seen: set[str] = set()
        posts: list[dict] = []
        stale_scrolls = 0

        while len(posts) < limit and stale_scrolls < 2:
            links = await page.query_selector_all('a[href*="/p/"], a[href*="/reel/"]')
            new_count = 0
            for link in links:
                href = await link.get_attribute("href") or ""
                if not href or href in seen:
                    continue
                seen.add(href)
                # Build a minimal post stub from grid info.
                full_url = f"https://www.instagram.com{href}" if href.startswith("/") else href
                stub: dict = {
                    "url": full_url,
                    "author": "",
                    "caption": "",
                    "date": "",
                    "likes": None,
                    "comments_count": None,
                    "media": [],
                    "top_comments": [],
                }
                # Try to get the image alt as a caption hint.
                try:
                    img = await link.query_selector("img")
                    if img:
                        stub["caption"] = (await img.get_attribute("alt")) or ""
                        src = await img.get_attribute("src") or ""
                        if src:
                            stub["media"] = [{"type": "image", "url": src.split("?")[0], "alt": stub["caption"]}]
                except Exception:  # noqa: BLE001
                    pass
                posts.append(stub)
                new_count += 1
                if len(posts) >= limit:
                    break

            if new_count == 0:
                stale_scrolls += 1
            else:
                stale_scrolls = 0

            # Human-speed scroll pause.
            await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
            await page.wait_for_timeout(1500)

        return posts

    @staticmethod
    def _render_profile_index(profile_data: dict, posts: list[dict], username: str) -> str:
        lines = [f"# Instagram profile: @{username}", ""]
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
            caption = post.get("caption", "")[:80]
            label = caption or "(no caption)"
            lines.append(f"{i + 1}. [{label}]({post.get('url', '')})")
        lines.append("")
        return "\n".join(lines)