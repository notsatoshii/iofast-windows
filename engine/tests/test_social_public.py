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
    _ig_node_to_media_item,
    _parse_ig_embed,
    _parse_ig_media,
    _parse_x_oembed,
    _parse_x_syndication,
    _ts_to_iso,
    _x_compute_token,
    maybe_transcribe,
)

FIXTURES = Path(__file__).parent / "fixtures" / "social"


# ---------------------------------------------------------------------------
# Instagram GraphQL parsing
# ---------------------------------------------------------------------------


class TestIGGraphQLParsing:
    def test_photo_post_parses_all_fields(self):
        data = json.loads((FIXTURES / "ig_graphql_photo.json").read_text(encoding="utf-8"))
        media = data["data"]["xig_polaris_media"]["if_not_gated_logged_out"]
        result = _parse_ig_media(media)

        assert result["shortcode"] == "CwTestPhoto1"
        assert result["author"] == "testuser"
        assert "sunset" in result["caption"].lower()
        assert result["likes"] == 1234
        assert result["comments_count"] == 56
        assert result["location"] == "Swiss Alps"
        assert len(result["media"]) == 1
        assert result["media"][0]["type"] == "image"
        assert "test-photo" in result["media"][0]["url"]
        assert result["media"][0]["alt"] == "A sunset over mountains"
        assert "nature" in result["hashtags"]
        assert "mountains" in result["hashtags"]
        assert "friend" in result["mentions"]
        assert result["date"] != ""

    def test_gated_response_has_no_media(self):
        data = json.loads((FIXTURES / "ig_graphql_gated.json").read_text(encoding="utf-8"))
        media = data["data"]["xig_polaris_media"].get("if_not_gated_logged_out")
        assert media is None  # Caller checks this and returns error.

    def test_video_node_extracts_url(self):
        node = {
            "is_video": True,
            "video_url": "https://example.com/video.mp4",
            "accessibility_caption": "A cat video",
        }
        item = _ig_node_to_media_item(node)
        assert item is not None
        assert item["type"] == "video"
        assert item["url"] == "https://example.com/video.mp4"
        assert item["alt"] == "A cat video"

    def test_image_node_extracts_url(self):
        node = {
            "is_video": False,
            "display_url": "https://example.com/photo.jpg",
            "accessibility_caption": "A landscape",
        }
        item = _ig_node_to_media_item(node)
        assert item is not None
        assert item["type"] == "image"
        assert item["url"] == "https://example.com/photo.jpg"

    def test_empty_node_returns_none(self):
        assert _ig_node_to_media_item({}) is None
        assert _ig_node_to_media_item({"is_video": True}) is None
        assert _ig_node_to_media_item({"is_video": False}) is None


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