"""Tests for vault ask: BM25 retrieval and index management."""
import json
from pathlib import Path


class TestChunkText:
    def test_short_text_single_chunk(self):
        from iofast_engine.vault.ask import _chunk_text
        chunks = _chunk_text("Hello world")
        assert len(chunks) == 1
        assert chunks[0] == "Hello world"

    def test_long_text_multiple_chunks(self):
        from iofast_engine.vault.ask import _chunk_text, CHUNK_SIZE
        text = "x" * (CHUNK_SIZE * 3)
        chunks = _chunk_text(text)
        assert len(chunks) >= 3


class TestExtractFrontmatter:
    def test_basic_frontmatter(self):
        from iofast_engine.vault.ask import _extract_frontmatter
        text = "---\ntags: [ai, ml]\naliases: [\"Test Alias\"]\n---\n# Title\nBody"
        fm, body = _extract_frontmatter(text)
        assert fm["tags"] == ["ai", "ml"]
        assert fm["aliases"] == ["Test Alias"]
        assert "# Title" in body

    def test_no_frontmatter(self):
        from iofast_engine.vault.ask import _extract_frontmatter
        fm, body = _extract_frontmatter("# Just a heading\nSome text")
        assert fm == {}
        assert "# Just a heading" in body


class TestUpdateIndex:
    def test_indexes_notes(self, tmp_path):
        from iofast_engine.vault.ask import update_index, _load_index
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "Test Note.md"
        note.write_text("---\ntags: [test]\n---\n# Test Note\nContent here.", encoding="utf-8")
        result = update_index(vault)
        assert result["updated"] >= 1
        index = _load_index(vault)
        assert "Test Note.md" in index["notes"]
        assert index["notes"]["Test Note.md"]["title"] == "Test Note"

    def test_skips_dotfiles(self, tmp_path):
        from iofast_engine.vault.ask import update_index, _load_index
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".hidden.md").write_text("# Hidden", encoding="utf-8")
        (vault / "Visible.md").write_text("# Visible", encoding="utf-8")
        update_index(vault)
        index = _load_index(vault)
        assert ".hidden.md" not in index["notes"]
        assert "Visible.md" in index["notes"]


class TestTokenize:
    def test_basic_tokenization(self):
        from iofast_engine.vault.ask import _tokenize
        tokens = _tokenize("Hello, World! This is a test.")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_lowercase(self):
        from iofast_engine.vault.ask import _tokenize
        tokens = _tokenize("UPPERCASE lower MiXeD")
        assert all(t == t.lower() for t in tokens)


class TestRetrieve:
    def test_retrieves_relevant_note(self, tmp_path):
        from iofast_engine.vault.ask import update_index, retrieve
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Python Guide.md").write_text(
            "---\ntags: [python]\n---\n# Python Guide\nLearn about Python programming.",
            encoding="utf-8",
        )
        (vault / "Rust Guide.md").write_text(
            "---\ntags: [rust]\n---\n# Rust Guide\nLearn about Rust systems programming.",
            encoding="utf-8",
        )
        update_index(vault)
        results = retrieve(vault, "Python programming")
        assert len(results) > 0
        assert results[0]["title"] == "Python Guide"

    def test_empty_vault_returns_empty(self, tmp_path):
        from iofast_engine.vault.ask import retrieve
        vault = tmp_path / "vault"
        vault.mkdir()
        results = retrieve(vault, "anything")
        assert results == []