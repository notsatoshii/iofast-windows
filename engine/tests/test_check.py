"""Tests for vault check: weekly health report generation."""
from datetime import date
from pathlib import Path


class TestIsoWeek:
    def test_returns_correct_format(self):
        from iofast_engine.vault.check import _iso_week
        result = _iso_week(date(2026, 10, 22))
        assert result.startswith("2026-W")
        assert len(result) == 8

    def test_default_is_today(self):
        from iofast_engine.vault.check import _iso_week
        result = _iso_week()
        expected = date.today().isocalendar()
        assert result == f"{expected[0]}-W{expected[1]:02d}"


class TestFindQuicknotes:
    def test_finds_notes_in_quicknotes(self, tmp_path):
        from iofast_engine.vault.check import _find_quicknotes
        vault = tmp_path / "vault"
        qn = vault / "Quicknotes (Unorganized)"
        qn.mkdir(parents=True)
        (qn / "Note One.md").write_text("# Note One\nContent", encoding="utf-8")
        (qn / "Note Two.md").write_text("# Note Two\nMore content", encoding="utf-8")
        results = _find_quicknotes(vault)
        assert len(results) == 2
        titles = {r["title"] for r in results}
        assert "Note One" in titles
        assert "Note Two" in titles

    def test_empty_when_no_quicknotes_dir(self, tmp_path):
        from iofast_engine.vault.check import _find_quicknotes
        vault = tmp_path / "vault"
        vault.mkdir()
        assert _find_quicknotes(vault) == []


class TestFindLikelyDuplicates:
    def test_detects_similar_titles(self, tmp_path):
        from iofast_engine.vault.check import _find_likely_duplicates
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Open AI.md").write_text("# Open AI", encoding="utf-8")
        (vault / "OpenAI.md").write_text("# OpenAI", encoding="utf-8")
        dupes = _find_likely_duplicates(vault)
        assert len(dupes) >= 1
        # normalize_title strips all non-word chars: "Open AI" -> "openai"
        found = any("openai" in d["normalized"] for d in dupes)
        assert found

    def test_no_duplicates_when_unique(self, tmp_path):
        from iofast_engine.vault.check import _find_likely_duplicates
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Python.md").write_text("# Python", encoding="utf-8")
        (vault / "Rust.md").write_text("# Rust", encoding="utf-8")
        dupes = _find_likely_duplicates(vault)
        assert dupes == []


class TestRunCheck:
    def test_writes_report_note(self, tmp_path):
        from iofast_engine.vault.check import run_check
        vault = tmp_path / "vault"
        vault.mkdir()
        # Create minimal structure
        (vault / "Research (RAW)" / "iofast").mkdir(parents=True)
        result = run_check(vault, week="2026-W43")
        assert result["week"] == "2026-W43"
        assert "report_path" in result
        report = Path(result["report_path"])
        assert report.exists()
        text = report.read_text(encoding="utf-8")
        assert "# 2026-W43 Vault Check" in text

    def test_idempotent_same_week(self, tmp_path):
        from iofast_engine.vault.check import run_check
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Research (RAW)" / "iofast").mkdir(parents=True)
        r1 = run_check(vault, week="2026-W43")
        r2 = run_check(vault, week="2026-W43")
        assert r1["report_path"] == r2["report_path"]