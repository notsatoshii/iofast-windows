"""Tests for vault digest: weekly digest generation."""
from datetime import date
from pathlib import Path


class TestWeekDateRange:
    def test_parses_iso_week(self):
        from iofast_engine.vault.digest import _week_date_range
        monday, sunday = _week_date_range("2026-W43")
        assert monday == date(2026, 10, 19)
        assert sunday == date(2026, 10, 25)

    def test_monday_is_monday(self):
        from iofast_engine.vault.digest import _week_date_range
        monday, _ = _week_date_range("2026-W01")
        assert monday.weekday() == 0  # Monday


class TestCollectWeekNotes:
    def test_finds_notes_in_week(self, tmp_path):
        from iofast_engine.vault.digest import _collect_week_notes
        vault = tmp_path / "vault"
        raw_dir = vault / "Research (RAW)" / "iofast"
        raw_dir.mkdir(parents=True)
        note = raw_dir / "2026-10-20 Test.md"
        note.write_text(
            "---\ncaptured: 2026-10-20\n---\n# Test Note\nContent.",
            encoding="utf-8",
        )
        notes = _collect_week_notes(vault, date(2026, 10, 19), date(2026, 10, 25))
        assert len(notes) == 1
        assert notes[0]["title"] == "Test Note"

    def test_excludes_out_of_week(self, tmp_path):
        from iofast_engine.vault.digest import _collect_week_notes
        vault = tmp_path / "vault"
        raw_dir = vault / "Research (RAW)" / "iofast"
        raw_dir.mkdir(parents=True)
        note = raw_dir / "2026-10-10 Old.md"
        note.write_text(
            "---\ncaptured: 2026-10-10\n---\n# Old Note\nContent.",
            encoding="utf-8",
        )
        notes = _collect_week_notes(vault, date(2026, 10, 19), date(2026, 10, 25))
        assert notes == []

    def test_skips_log_md(self, tmp_path):
        from iofast_engine.vault.digest import _collect_week_notes
        vault = tmp_path / "vault"
        raw_dir = vault / "Research (RAW)" / "iofast"
        raw_dir.mkdir(parents=True)
        (raw_dir / "log.md").write_text("- 2026-10-20 · entry", encoding="utf-8")
        notes = _collect_week_notes(vault, date(2026, 10, 19), date(2026, 10, 25))
        assert notes == []


class TestCollectLogEntries:
    def test_finds_entries_in_week(self, tmp_path):
        from iofast_engine.vault.digest import _collect_log_entries
        vault = tmp_path / "vault"
        raw_dir = vault / "Research (RAW)" / "iofast"
        raw_dir.mkdir(parents=True)
        log = raw_dir / "log.md"
        log.write_text(
            "---\ntags: [iofast, log]\n---\n\n# iofast capture log\n\n"
            "- 2026-10-20 · [[Test]] · web · AI · <https://example.com>\n"
            "- 2026-10-10 · [[Old]] · web · AI · <https://old.com>\n",
            encoding="utf-8",
        )
        entries = _collect_log_entries(vault, date(2026, 10, 19), date(2026, 10, 25))
        assert len(entries) == 1
        assert "2026-10-20" in entries[0]


class TestMakeDigest:
    def test_writes_digest_note(self, tmp_path, monkeypatch):
        from iofast_engine.vault.digest import make_digest
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Research (RAW)" / "iofast").mkdir(parents=True)
        # Mock LLM call since we're offline
        monkeypatch.setattr(
            "iofast_engine.vault.digest._call_llm",
            lambda prompt: "## Themes\n- Test theme\n\n## Best Items\n- [[Test]]",
        )
        result = make_digest(vault, week="2026-W43")
        assert result["week"] == "2026-W43"
        assert "digest_path" in result
        digest = Path(result["digest_path"])
        assert digest.exists()
        text = digest.read_text(encoding="utf-8")
        assert "# 2026-W43 Digest" in text

    def test_idempotent_same_week(self, tmp_path, monkeypatch):
        from iofast_engine.vault.digest import make_digest
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Research (RAW)" / "iofast").mkdir(parents=True)
        monkeypatch.setattr(
            "iofast_engine.vault.digest._call_llm",
            lambda prompt: "Digest content",
        )
        r1 = make_digest(vault, week="2026-W43")
        r2 = make_digest(vault, week="2026-W43")
        assert r1["digest_path"] == r2["digest_path"]

    def test_empty_week_writes_placeholder(self, tmp_path, monkeypatch):
        from iofast_engine.vault.digest import make_digest
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Research (RAW)" / "iofast").mkdir(parents=True)
        # No LLM call needed for empty week
        result = make_digest(vault, week="2026-W43")
        digest = Path(result["digest_path"])
        assert digest.exists()
        text = digest.read_text(encoding="utf-8")
        assert "No captures this week" in text