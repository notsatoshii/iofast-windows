"""Tests for vault dedupe: URL normalization and entity title matching."""
import json
import pytest
from pathlib import Path


class TestNormalizeUrl:
    def test_youtube_shorts(self):
        from iofast_engine.vault.dedupe import normalize_url
        assert normalize_url("https://www.youtube.com/shorts/abc123") == \
            "https://youtube.com/watch?v=abc123"

    def test_youtu_be(self):
        from iofast_engine.vault.dedupe import normalize_url
        assert normalize_url("https://youtu.be/xyz789") == \
            "https://youtube.com/watch?v=xyz789"

    def test_youtube_mobile(self):
        from iofast_engine.vault.dedupe import normalize_url
        assert normalize_url("https://m.youtube.com/watch?v=test123&feature=share") == \
            "https://youtube.com/watch?v=test123"

    def test_twitter_to_x(self):
        from iofast_engine.vault.dedupe import normalize_url
        assert normalize_url("https://twitter.com/user/status/123") == \
            "https://x.com/user/status/123"

    def test_instagram_reel_to_p(self):
        from iofast_engine.vault.dedupe import normalize_url
        result = normalize_url("https://www.instagram.com/reel/ABCdef/")
        assert result == "https://instagram.com/p/ABCdef"

    def test_drop_tracking_params(self):
        from iofast_engine.vault.dedupe import normalize_url
        result = normalize_url("https://example.com/page?utm_source=twitter&real=param")
        assert "utm_source" not in result
        assert "real=param" in result

    def test_strip_trailing_slash(self):
        from iofast_engine.vault.dedupe import normalize_url
        assert normalize_url("https://example.com/path/") == "https://example.com/path"

    def test_empty_url(self):
        from iofast_engine.vault.dedupe import normalize_url
        assert normalize_url("") == ""
        assert normalize_url("   ") == ""


class TestContentHash:
    def test_same_text_same_hash(self):
        from iofast_engine.vault.dedupe import content_hash
        assert content_hash("Hello World") == content_hash("Hello World")

    def test_whitespace_normalized(self):
        from iofast_engine.vault.dedupe import content_hash
        assert content_hash("Hello  World") == content_hash("Hello World")

    def test_case_insensitive(self):
        from iofast_engine.vault.dedupe import content_hash
        assert content_hash("Hello World") == content_hash("hello world")

    def test_different_text_different_hash(self):
        from iofast_engine.vault.dedupe import content_hash
        assert content_hash("Hello") != content_hash("World")


class TestNormalizeTitle:
    def test_basic_normalization(self):
        from iofast_engine.vault.dedupe import normalize_title
        # All non-word chars stripped: "Open AI" -> "openai"
        assert normalize_title("Open AI") == "openai"

    def test_punctuation_stripped(self):
        from iofast_engine.vault.dedupe import normalize_title
        # All non-word chars stripped: "Hello, World!" -> "helloworld"
        assert normalize_title("Hello, World!") == "helloworld"

    def test_simple_plural(self):
        from iofast_engine.vault.dedupe import normalize_title
        assert normalize_title("Transformers") == "transformer"

    def test_no_over_strip(self):
        from iofast_engine.vault.dedupe import normalize_title
        # Single char after strip should keep original
        assert normalize_title("As") == "as"


class TestFindExistingEntity:
    def test_exact_match(self):
        from iofast_engine.vault.dedupe import find_existing_entity
        existing = {"Python", "JavaScript", "Rust"}
        assert find_existing_entity("Python", existing) == "Python"

    def test_case_insensitive_match(self):
        from iofast_engine.vault.dedupe import find_existing_entity
        existing = {"OpenAI", "Google"}
        assert find_existing_entity("openai", existing) == "OpenAI"

    def test_spacing_variant(self):
        from iofast_engine.vault.dedupe import find_existing_entity
        existing = {"Open AI", "Deep Mind"}
        assert find_existing_entity("OpenAI", existing) == "Open AI"

    def test_no_match(self):
        from iofast_engine.vault.dedupe import find_existing_entity
        existing = {"Python", "Rust"}
        assert find_existing_entity("JavaScript", existing) is None


class TestCheckDuplicate:
    def test_vault_source_match(self, tmp_path):
        from iofast_engine.vault.dedupe import check_duplicate
        vault = tmp_path / "vault"
        raw_dir = vault / "Research (RAW)" / "iofast"
        raw_dir.mkdir(parents=True)
        note = raw_dir / "2026-01-01 Test.md"
        note.write_text(
            "---\nsource: \"https://youtube.com/watch?v=abc\"\n---\n# Test\n",
            encoding="utf-8",
        )
        result = check_duplicate(
            "https://youtu.be/abc",
            vault_path=vault,
        )
        assert result is not None
        assert result["match_type"] == "url"
        assert str(note) in result["vault_note"]

    def test_no_duplicate(self, tmp_path):
        from iofast_engine.vault.dedupe import check_duplicate
        vault = tmp_path / "vault"
        vault.mkdir()
        result = check_duplicate("https://example.com/new", vault_path=vault)
        assert result is None