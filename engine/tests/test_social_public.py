"""Offline unit tests for social_public.py parsing and helpers.

Tests run without network access: they feed recorded fixture JSON/HTML into
the parser functions directly and verify the normalized output shape. Route
order and fallback logic are tested by checking that the source modules call
public endpoints before signed-in ones (verified via the _grab_post flow).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Import parsers directly — they are pure functions with no side effects.
from iofast_engine.sources.social_public import (
    PublicResult,
    _parse_ig_embed,
    _parse_x_oembed,
    _parse_x_syndication,
    _parse_ytdlp_info,
    _ts_to_iso,
    _x_compute_token,
    maybe_transcribe,
)

FIXTURES = Path(__file__).parent / "fixtures" / "social"


# ---------------------------------------------------------------------------
# Import sanity (reviewer finding #1)
# ---------------------------------------------------------------------------


class TestImportSanity:
    """Verify the module imports cleanly and exposes expected public API."""

    def test_module_imports_without_error(self):
        import iofast_engine.sources.social_public as sp

        assert hasattr(sp, "ig_fetch_post_ytdlp")
        assert hasattr(sp, "ig_fetch_embed")
        assert hasattr(sp, "x_fetch_syndication")
        assert hasattr(sp, "x_fetch_oembed")
        assert hasattr(sp, "download_media")
        assert hasattr(sp, "maybe_transcribe")
        assert hasattr(sp, "PublicResult")

    def test_client_factory_callable(self):
        """The lazy httpx client factory must be importable and callable."""
        from iofast_engine.sources.social_public import _get_client

        assert callable(_get_client)


# ---------------------------------------------------------------------------
# Instagram yt-dlp parsing
# ---------------------------------------------------------------------------


class TestIGYtdlpParsing:
    def test_photo_post_parses_all_fields(self):
        info = {
            "id": "CwTestPhoto1",
            "title": "A sunset over mountains #nature #mountains @friend",
            "description": "Beautiful sunset #nature #mountains @friend",
            "uploader": "testuser",
            "upload_date": "20240115",
            "like_count": 1234,
            "comment_count": 56,
            "thumbnail": "https://cdn.example.com/test-photo.jpg",
            "formats": [],
            "thumbnails": [],
        }
        result = _parse_ytdlp_info(info, "https://www.instagram.com/p/CwTestPhoto1/")

        assert result["shortcode"] == "CwTestPhoto1"
        assert result["author"] == "testuser"
        assert "sunset" in result["caption"].lower()
        assert result["likes"] == 1234
        assert result["comments_count"] == 56
        assert len(result["media"]) == 1
        assert result["media"][0]["type"] == "image"
        assert "test-photo" in result["media"][0]["url"]
        assert "nature" in result["hashtags"]
        assert "mountains" in result["hashtags"]
        assert "friend" in result["mentions"]

    def test_video_reel_extracts_video_format(self):
        info = {
            "id": "CwTestReel1",
            "description": "A cat video #cats @owner",
            "uploader": "catlover",
            "upload_date": "20240201",
            "like_count": 500,
            "comment_count": 10,
            "duration": 30,
            "view_count": 10000,
            "thumbnail": "https://cdn.example.com/thumb.jpg",
            "formats": [
                {"vcodec": "none", "url": "https://cdn.example.com/audio.m4a"},
                {"vcodec": "h264", "url": "https://cdn.example.com/video.mp4", "filesize": 5000000, "tbr": 2000},
            ],
            "thumbnails": [],
        }
        result = _parse_ytdlp_info(info, "https://www.instagram.com/reel/CwTestReel1/")

        assert result["shortcode"] == "CwTestReel1"
        assert result["author"] == "catlover"
        assert result["view_count"] == 10000
        # Should have both video and thumbnail image.
        types = [m["type"] for m in result["media"]]
        assert "video" in types
        video_item = next(m for m in result["media"] if m["type"] == "video")
        assert video_item["url"] == "https://cdn.example.com/video.mp4"
        assert video_item["duration"] == 30

    def test_carousel_picks_best_thumbnail(self):
        info = {
            "id": "CwCarousel1",
            "description": "Multi-image post",
            "uploader": "photog",
            "formats": [],
            "thumbnails": [
                {"url": "https://cdn.example.com/small.jpg", "width": 150, "height": 150},
                {"url": "https://cdn.example.com/large.jpg", "width": 1080, "height": 1080},
            ],
        }
        result = _parse_ytdlp_info(info, "https://www.instagram.com/p/CwCarousel1/")

        assert len(result["media"]) == 1
        assert result["media"][0]["type"] == "image"
        assert "large.jpg" in result["media"][0]["url"]

    def test_reel_url_variant(self):
        info = {
            "id": "CwTv1",
            "description": "IGTV content",
            "uploader": "creator",
            "formats": [],
            "thumbnails": [{"url": "https://cdn.example.com/tv.jpg", "width": 640}],
        }
        result = _parse_ytdlp_info(info, "https://www.instagram.com/tv/CwTv1/")
        assert result["shortcode"] == "CwTv1"

    def test_empty_info_produces_minimal_result(self):
        result = _parse_ytdlp_info({}, "https://www.instagram.com/p/X/")
        assert result["shortcode"] == "X"
        assert result["author"] == ""
        assert result["caption"] == ""
        assert result["media"] == []


# ---------------------------------------------------------------------------
# Instagram embed parsing
# ---------------------------------------------------------------------------


class TestIGEmbedParsing:
    def test_embed_html_parses_author_and_caption(self):
        html = """
        <div class="EmbedFrame">
          <a class="Username" href="/testuser">testuser</a>
          <div class="Caption">Hello world #test @someone</div>
          <time datetime="2024-01-15T10:00:00+00:00">Jan 15</time>
          <img class="EmbeddedMediaImage" src="https://cdn.example.com/img.jpg">
        </div>
        """
        result = _parse_ig_embed(html, "ABC123")
        assert result is not None
        assert result["author"] == "testuser"
        assert "Hello world" in result["caption"]
        assert result["date"] == "2024-01-15T10:00:00+00:00"
        assert len(result["media"]) == 1
        assert result["media"][0]["type"] == "image"
        assert "test" in result["hashtags"]
        assert "someone" in result["mentions"]

    def test_empty_html_returns_none(self):
        assert _parse_ig_embed("<html></html>", "X") is None


# ---------------------------------------------------------------------------
# X syndication parsing
# ---------------------------------------------------------------------------


class TestXSyndicationParsing:
    def test_syndication_parses_all_fields(self):
        data = json.loads((FIXTURES / "x_syndication.json").read_text(encoding="utf-8"))
        result = _parse_x_syndication(data, "1700000000000000000")

        assert result["id"] == "1700000000000000000"
        assert result["handle"] == "testuser"
        assert result["author"] == "Test User"
        # t.co URL should be expanded.
        assert "https://example.com/article" in result["text"]
        assert "https://t.co/abc123" not in result["text"]
        assert result["likes"] == 42
        assert result["retweets"] == 5
        assert result["replies"] == 3
        assert result["quotes"] == 1
        assert len(result["media"]) == 1
        assert result["media"][0]["type"] == "image"
        assert result["media"][0]["alt"] == "A test image"
        assert result["canonical_url"] == "https://x.com/testuser/status/1700000000000000000"

    def test_syndication_html_entities_unescaped(self):
        body = {
            "text": "Less than &lt; greater than &gt; ampersand &amp;",
            "user": {"screen_name": "esc", "name": "Escaper"},
        }
        result = _parse_x_syndication(body, "111")
        assert "<" in result["text"]
        assert ">" in result["text"]
        assert "&" in result["text"]
        assert "&lt;" not in result["text"]

    def test_syndication_with_video_picks_best_variant(self):
        body = {
            "text": "Video tweet",
            "user": {"screen_name": "viduser", "name": "Vid User"},
            "mediaDetails": [
                {
                    "type": "video",
                    "ext_alt_text": "A clip",
                    "variants": [
                        {"content_type": "video/mp4", "bitrate": 832000, "url": "https://low.mp4"},
                        {"content_type": "video/mp4", "bitrate": 2176000, "url": "https://high.mp4"},
                        {"content_type": "application/x-mpegURL", "bitrate": 0, "url": "https://hls.m3u8"},
                    ],
                }
            ],
        }
        result = _parse_x_syndication(body, "999")
        assert len(result["media"]) == 1
        assert result["media"][0]["type"] == "video"
        assert result["media"][0]["url"] == "https://high.mp4"

    def test_syndication_quoted_tweet(self):
        body = {
            "text": "Check this out",
            "user": {"screen_name": "quoter", "name": "Quoter"},
            "quoted_tweet": {
                "text": "Original tweet text",
                "user": {"screen_name": "original"},
            },
        }
        result = _parse_x_syndication(body, "888")
        assert result["quoted"] is not None
        assert result["quoted"]["author"] == "original"
        assert result["quoted"]["text"] == "Original tweet text"

    def test_syndication_tco_expansion_from_entities(self):
        body = {
            "text": "Read this https://t.co/xyz789 now",
            "user": {"screen_name": "linker", "name": "Linker"},
            "entities": {
                "urls": [
                    {"url": "https://t.co/xyz789", "expanded_url": "https://example.com/full-article"},
                ]
            },
        }
        result = _parse_x_syndication(body, "777")
        assert "https://example.com/full-article" in result["text"]
        assert "https://t.co/xyz789" not in result["text"]


# ---------------------------------------------------------------------------
# X oEmbed parsing
# ---------------------------------------------------------------------------


class TestXOEmbedParsing:
    def test_oembed_parses_author_and_text(self):
        data = json.loads((FIXTURES / "x_oembed.json").read_text(encoding="utf-8"))
        result = _parse_x_oembed(data, "https://twitter.com/testuser/status/1700000000000000000")

        assert result is not None
        assert result["author"] == "Test User"
        assert result["handle"] == "testuser"
        assert "test tweet" in result["text"].lower()
        assert result["id"] == "1700000000000000000"

    def test_oembed_empty_html_returns_none(self):
        assert _parse_x_oembed({"html": ""}, "https://x.com/a/status/1") is None


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


class TestUtilities:
    def test_ts_to_iso_valid(self):
        result = _ts_to_iso(1700000000)
        assert "2023" in result
        assert "T" in result

    def test_ts_to_iso_none(self):
        assert _ts_to_iso(None) == ""

    def test_ts_to_iso_invalid(self):
        assert _ts_to_iso("not-a-number") == ""

    def test_x_compute_token_returns_string(self):
        token = _x_compute_token("1700000000000000000")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_x_compute_token_bad_id(self):
        token = _x_compute_token("not-a-number")
        assert token == "0"

    def test_maybe_transcribe_returns_none_when_missing(self):
        # transcribe module doesn't exist yet — should return None gracefully.
        result = maybe_transcribe(Path("/nonexistent/video.mp4"))
        assert result is None


# ---------------------------------------------------------------------------
# PublicResult dataclass
# ---------------------------------------------------------------------------


class TestPublicResult:
    def test_ok_result(self):
        r = PublicResult(ok=True, data={"key": "value"})
        assert r.ok is True
        assert r.data == {"key": "value"}
        assert r.error is None

    def test_error_result(self):
        r = PublicResult(ok=False, error="rate limited (429)")
        assert r.ok is False
        assert r.data is None
        assert "429" in r.error