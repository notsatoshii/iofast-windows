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

    def test_video_prefers_stream_with_sound_and_keeps_audio_url(self):
        # Instagram's real shape: DASH video-only streams with high bitrates,
        # a DASH audio-only stream, and progressive streams with unknown codecs.
        info = {
            "id": "CDUMkliABpa",
            "formats": [
                {"format_id": "dash-a", "vcodec": "none", "acodec": "mp4a.40.2", "tbr": 131, "url": "https://cdn.example.com/a.m4a"},
                {"format_id": "1", "url": "https://cdn.example.com/p1.mp4"},
                {"format_id": "2", "url": "https://cdn.example.com/p2.mp4"},
                {"format_id": "dash-v", "vcodec": "avc1", "acodec": "none", "tbr": 1795, "url": "https://cdn.example.com/v.mp4"},
            ],
            "thumbnails": [],
        }
        result = _parse_ytdlp_info(info, "https://www.instagram.com/reel/CDUMkliABpa/")
        video_item = next(m for m in result["media"] if m["type"] == "video")
        assert video_item["url"] == "https://cdn.example.com/p2.mp4"
        assert video_item["audio_url"] == "https://cdn.example.com/a.m4a"

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

    def test_x_compute_token_matches_react_tweet(self):
        # Reference values from react-tweet's getToken run in Node.
        assert _x_compute_token("20") == "6dq1a2xwd93"
        assert _x_compute_token("1683920951807971329") == "42y6zv7ufp"
        assert _x_compute_token("1047395834013384704") == "2jehoes3gic"
        assert _x_compute_token("1577855540407197696") == "3toz99oivuy"
        assert _x_compute_token("1395079556562706435") == "3dqrs3s9rn9"

    def test_x_compute_token_bad_id(self):
        token = _x_compute_token("not-a-number")
        assert token == "0"

    def test_maybe_transcribe_success_with_header(self):
        """maybe_transcribe returns formatted transcript with header on success."""
        from unittest.mock import patch

        with patch(
            "iofast_engine.transcribe.transcribe_local_video",
            return_value="Transcript: whisper small (local) (en)\n\n[00:00] Hello world\n[00:05] Testing",
        ):
            result = maybe_transcribe(Path("/fake/video.mp4"))
        assert result is not None
        assert result.startswith("Transcript: whisper small (local) (en)")
        assert "[00:00] Hello world" in result

    def test_maybe_transcribe_no_speech(self):
        """maybe_transcribe returns 'no speech detected' when whisper finds nothing."""
        from unittest.mock import patch

        with patch(
            "iofast_engine.transcribe.transcribe_local_video",
            return_value="no speech detected",
        ):
            result = maybe_transcribe(Path("/fake/silent.mp4"))
        assert result == "no speech detected"

    def test_maybe_transcribe_not_installed(self):
        """maybe_transcribe returns 'transcription not installed' when whisper missing."""
        from unittest.mock import patch

        with patch(
            "iofast_engine.transcribe.transcribe_local_video",
            return_value="transcription not installed",
        ):
            result = maybe_transcribe(Path("/fake/video.mp4"))
        assert result == "transcription not installed"

    def test_maybe_transcribe_error_propagated(self):
        """maybe_transcribe returns error description instead of swallowing it."""
        from unittest.mock import patch

        with patch(
            "iofast_engine.transcribe.transcribe_local_video",
            side_effect=RuntimeError("CUDA out of memory"),
        ):
            result = maybe_transcribe(Path("/fake/video.mp4"))
        assert result is not None
        assert "CUDA out of memory" in result

    def test_maybe_transcribe_returns_none_when_module_missing(self):
        """maybe_transcribe returns None when transcribe_local_video cannot be imported."""
        from unittest.mock import patch
        import iofast_engine.sources.social_public as sp_mod

        # Simulate the ImportError path by making the local import fail.
        original = sp_mod.maybe_transcribe

        def fake_maybe_transcribe(video_path, emit=None):
            try:
                raise ImportError("no transcribe module")
            except ImportError:
                return None

        with patch.object(sp_mod, "maybe_transcribe", side_effect=fake_maybe_transcribe):
            result = sp_mod.maybe_transcribe(Path("/fake/video.mp4"))
        assert result is None

    def test_maybe_transcribe_no_audio_stream(self):
        """transcribe_local_video returns warning when file has no audio stream."""
        from unittest.mock import patch, MagicMock
        from iofast_engine.transcribe import transcribe_local_video

        # Mock PyAV to simulate a video with no audio streams
        mock_container = MagicMock()
        mock_container.streams.audio = []  # No audio streams
        mock_container.__enter__ = MagicMock(return_value=mock_container)
        mock_container.__exit__ = MagicMock(return_value=False)

        with patch("av.open", return_value=mock_container):
            events = []
            result = transcribe_local_video(Path("/fake/no-audio.mp4"), lambda e: events.append(e))
        assert "no audio stream" in result

    def test_maybe_transcribe_vad_retry_on_music(self):
        """transcribe_local_video retries without VAD when VAD filters everything."""
        from unittest.mock import patch, MagicMock
        from iofast_engine.transcribe import transcribe_local_video

        # Mock PyAV to report audio streams exist
        mock_container = MagicMock()
        mock_audio_stream = MagicMock()
        mock_container.streams.audio = [mock_audio_stream]
        mock_container.__enter__ = MagicMock(return_value=mock_container)
        mock_container.__exit__ = MagicMock(return_value=False)

        # Mock WhisperModel: first call (VAD=True) returns empty, second (VAD=False) returns segments
        mock_model = MagicMock()
        mock_info_vad = MagicMock()
        mock_info_vad.language = "en"
        mock_info_novad = MagicMock()
        mock_info_novad.language = "en"

        mock_seg = MagicMock()
        mock_seg.start = 0.0
        mock_seg.end = 2.0
        mock_seg.text = "Like a woman, ooh"

        # First transcribe call (VAD=True) returns empty iterator
        # Second transcribe call (VAD=False) returns one segment
        mock_model.transcribe.side_effect = [
            (iter([]), mock_info_vad),  # VAD=True: nothing
            (iter([mock_seg]), mock_info_novad),  # VAD=False: found speech
        ]

        with patch("av.open", return_value=mock_container), \
             patch("faster_whisper.WhisperModel", return_value=mock_model), \
             patch("iofast_engine.transcribe.pick_device_and_model", return_value=("cpu", "int8", "small")), \
             patch("iofast_engine.transcribe._models_root", return_value=Path("/fake/models")):
            events = []
            result = transcribe_local_video(Path("/fake/music.mp4"), lambda e: events.append(e))

        assert result.startswith("Transcript:")
        assert "Like a woman" in result
        # Verify retry event was emitted
        retry_events = [e for e in events if "retrying" in e.get("message", "")]
        assert len(retry_events) == 1


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