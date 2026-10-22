"""Offline tests for the sources package (web, youtube, github).

All tests use committed fixtures — no live web calls. The live smoke script
(engine/tools/sources_smoke.py) exercises real endpoints separately.
"""

import json
import os
from pathlib import Path

import pytest

from iofast_engine.sources import GrabOptions, detect
from iofast_engine.writer import UNTRUSTED_BANNER

FIXTURES = Path(__file__).parent / "fixtures" / "sources"


# --- detect() routing -------------------------------------------------------

class TestDetect:
    def test_youtube_watch(self):
        assert detect("https://www.youtube.com/watch?v=dQw4w9WgXcQ").name == "youtube"

    def test_youtube_shorts(self):
        assert detect("https://youtube.com/shorts/abc123").name == "youtube"

    def test_youtu_be(self):
        assert detect("https://youtu.be/dQw4w9WgXcQ").name == "youtube"

    def test_youtube_playlist(self):
        assert detect("https://www.youtube.com/playlist?list=PLtest").name == "youtube"

    def test_github_repo(self):
        assert detect("https://github.com/owner/repo").name == "github"

    def test_github_tree(self):
        assert detect("https://github.com/owner/repo/tree/main/src").name == "github"

    def test_github_blob(self):
        assert detect("https://github.com/owner/repo/blob/main/README.md").name == "github"

    def test_generic_url_falls_back_to_web(self):
        assert detect("https://example.com/blog/post").name == "web"

    def test_news_site_falls_back_to_web(self):
        assert detect("https://arstechnica.com/science/2024/article").name == "web"


# --- fixture sanity ---------------------------------------------------------

class TestFixtures:
    """Verify committed fixtures parse correctly (guards against corruption)."""

    def test_yt_video_info_parses(self):
        data = json.loads((FIXTURES / "yt_video_info.json").read_text(encoding="utf-8"))
        assert data["id"] == "dQw4w9WgXcQ"
        assert "en" in data["subtitles"]

    def test_yt_caption_parses(self):
        text = (FIXTURES / "yt_caption_en.vtt").read_text(encoding="utf-8")
        assert "WEBVTT" in text
        assert "strangers to love" in text

    def test_github_meta_parses(self):
        data = json.loads((FIXTURES / "github_repo_meta.json").read_text(encoding="utf-8"))
        assert data["full_name"] == "test-owner/test-repo"
        assert data["stargazers_count"] == 42

    def test_article_html_has_main(self):
        text = (FIXTURES / "article.html").read_text(encoding="utf-8")
        assert "<main>" in text
        assert "<article>" in text


# --- envelope contract ------------------------------------------------------

class TestEnvelopeContract:
    """Every source must write files with SOURCE line + UNTRUSTED banner."""

    def test_untrusted_banner_constant_exists(self):
        assert len(UNTRUSTED_BANNER) > 20
        assert "UNTRUSTED" in UNTRUSTED_BANNER

    def test_grab_options_defaults(self):
        opts = GrabOptions()
        assert opts.mode == "page"
        assert opts.limit is None
        assert opts.lang is None

    def test_grab_options_custom(self):
        opts = GrabOptions(mode="site", limit=10, lang="ko")
        assert opts.mode == "site"
        assert opts.limit == 10
        assert opts.lang == "ko"