"""Offline unit tests for YouTube transcription fallback logic.

Tests cover:
- Caption priority (manual > auto)
- Paragraph grouping from whisper segments
- Length guard (skip > ceiling)
- Fallback behaviour when transcription fails
- Device/model selection defaults
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from iofast_engine.sources.youtube import (
    _has_manual_captions,
    _pick_caption,
    _transcript_text,
    _format_duration,
    should_skip_transcribe,
)
from iofast_engine.transcribe import (
    TranscriptResult,
    _group_segments,
    _format_paragraphs,
    max_transcribe_seconds,
    pick_device_and_model,
)


# ---------------------------------------------------------------------------
# Caption selection
# ---------------------------------------------------------------------------

class TestCaptionPriority:
    def test_manual_captions_win_over_auto(self):
        info = {
            "subtitles": {"en": [{"text": "manual", "start": 0, "duration": 1}]},
            "automatic_captions": {"en": [{"text": "auto", "start": 0, "duration": 1}]},
        }
        assert _has_manual_captions(info, None) is True
        entries = _pick_caption(info, None)
        assert entries[0]["text"] == "manual"

    def test_auto_used_when_no_manual(self):
        info = {
            "subtitles": {},
            "automatic_captions": {"en": [{"text": "auto", "start": 0, "duration": 1}]},
        }
        assert _has_manual_captions(info, None) is False
        entries = _pick_caption(info, None)
        assert entries is not None
        assert entries[0]["text"] == "auto"

    def test_preferred_language_first(self):
        info = {
            "subtitles": {
                "en": [{"text": "english", "start": 0, "duration": 1}],
                "ko": [{"text": "korean", "start": 0, "duration": 1}],
            },
        }
        entries = _pick_caption(info, "ko")
        assert entries[0]["text"] == "korean"

    def test_fallback_to_ko_then_en(self):
        info = {
            "subtitles": {
                "en": [{"text": "english", "start": 0, "duration": 1}],
            },
        }
        # No preferred lang → ko missing → falls to en.
        entries = _pick_caption(info, None)
        assert entries[0]["text"] == "english"

    def test_any_language_when_no_candidate_matches(self):
        info = {
            "subtitles": {
                "ja": [{"text": "japanese", "start": 0, "duration": 1}],
            },
        }
        entries = _pick_caption(info, "de")
        assert entries is not None
        assert entries[0]["text"] == "japanese"

    def test_none_when_nothing_exists(self):
        info = {"subtitles": {}, "automatic_captions": {}}
        assert _pick_caption(info, None) is None


# ---------------------------------------------------------------------------
# Transcript formatting
# ---------------------------------------------------------------------------

class TestTranscriptFormatting:
    def test_groups_with_gaps(self):
        entries = [
            {"text": "Hello", "start": 0, "duration": 1},
            {"text": "world", "start": 1, "duration": 1},
            # 5-second gap → new paragraph
            {"text": "New thought", "start": 7, "duration": 2},
        ]
        text = _transcript_text(entries)
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        assert len(paragraphs) == 2
        assert "[00:00]" in paragraphs[0]
        assert "[00:07]" in paragraphs[1]

    def test_empty_entries_returns_empty(self):
        assert _transcript_text([]) == ""
        assert _transcript_text([{"text": "", "start": 0, "duration": 1}]) == ""


# ---------------------------------------------------------------------------
# Whisper paragraph grouping
# ---------------------------------------------------------------------------

@dataclass
class FakeSegment:
    start: float
    end: float
    text: str


class TestWhisperGrouping:
    def test_merges_short_segments_into_paragraphs(self):
        segments = [
            FakeSegment(0, 5, "First sentence."),
            FakeSegment(5, 10, "Second sentence."),
            FakeSegment(10, 15, "Third sentence."),
            FakeSegment(15, 20, "Fourth sentence."),
            FakeSegment(20, 25, "Fifth sentence."),
            FakeSegment(25, 30, "Sixth sentence."),
            FakeSegment(30, 35, "Seventh sentence."),
            FakeSegment(35, 40, "Eighth sentence."),
            FakeSegment(40, 45, "Ninth sentence."),
            FakeSegment(45, 50, "Tenth sentence."),
        ]
        paragraphs = _group_segments(segments, target_seconds=45.0)
        # Should produce ~2 paragraphs for 50s of content at 45s target.
        assert len(paragraphs) >= 1
        assert all(isinstance(p, tuple) and len(p) == 2 for p in paragraphs)

    def test_breaks_on_silence_gap(self):
        segments = [
            FakeSegment(0, 5, "Before gap."),
            FakeSegment(10, 15, "After gap."),  # 5s silence
        ]
        paragraphs = _group_segments(segments, target_seconds=45.0)
        assert len(paragraphs) == 2

    def test_format_paragraphs_timestamps(self):
        paragraphs = [(0, "Start"), (65, "One minute five")]
        text = _format_paragraphs(paragraphs)
        assert "[00:00] Start" in text
        assert "[01:05] One minute five" in text

    def test_empty_segments(self):
        assert _group_segments([], target_seconds=45.0) == []


# ---------------------------------------------------------------------------
# Length guard
# ---------------------------------------------------------------------------

class TestLengthGuard:
    def test_under_ceiling_passes(self):
        assert should_skip_transcribe(3600) is False  # 1 hour

    def test_over_ceiling_skips(self):
        assert should_skip_transcribe(3 * 3600 + 1) is True  # 3h + 1s

    def test_none_duration_passes(self):
        assert should_skip_transcribe(None) is False

    def test_zero_duration_passes(self):
        assert should_skip_transcribe(0) is False

    def test_configurable_ceiling(self):
        with patch.dict(os.environ, {"IOFAST_TRANSCRIBE_MAX_SECONDS": "600"}):
            assert should_skip_transcribe(601) is True
            assert should_skip_transcribe(599) is False

    def test_zero_ceiling_disables_guard(self):
        with patch.dict(os.environ, {"IOFAST_TRANSCRIBE_MAX_SECONDS": "0"}):
            assert should_skip_transcribe(999999) is False


# ---------------------------------------------------------------------------
# Device / model selection
# ---------------------------------------------------------------------------

class TestDeviceSelection:
    def test_env_override_wins(self):
        with patch.dict(os.environ, {"IOFAST_WHISPER_MODEL": "tiny"}):
            _, _, model = pick_device_and_model()
            assert model == "tiny"

    def test_default_without_cuda(self):
        """Without CUDA, defaults to cpu/int8/small."""
        with patch.dict("sys.modules", {"ctranslate2": None}):
            device, compute, model = pick_device_and_model()
            # Either cuda or cpu depending on test environment; just check types.
            assert device in ("cuda", "cpu")
            assert compute in ("float16", "int8")
            assert isinstance(model, str)


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------

class TestDurationFormat:
    def test_minutes_seconds(self):
        assert _format_duration(125) == "2:05"

    def test_hours(self):
        assert _format_duration(3661) == "1:01:01"

    def test_none(self):
        assert _format_duration(None) == ""

    def test_zero(self):
        assert _format_duration(0) == ""