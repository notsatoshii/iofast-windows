"""Tests for AgentOS topic-hub matching and vault-scan exclusions."""

import json
from pathlib import Path

import pytest

from iofast_engine.vault.filer import match_topic_hubs
from iofast_engine.vault.scan import scan_vault


@pytest.fixture
def topics_json(tmp_path: Path) -> Path:
    """Create a fake AgentOS/topics.json with three hubs."""
    agent_os = tmp_path / "AgentOS"
    agent_os.mkdir()
    hubs = [
        {
            "slug": "llm-agents",
            "title": "LLM Agents",
            "path": "AgentOS/Topics/LLM Agents",
            "keywords": ["agent", "llm", "tool-use", "orchestration"],
        },
        {
            "slug": "rust-ffi",
            "title": "Rust FFI",
            "path": "AgentOS/Topics/Rust FFI",
            "keywords": ["rust", "ffi", "pyo3", "tauri"],
        },
        {
            "slug": "obsidian-plugins",
            "title": "Obsidian Plugins",
            "path": "AgentOS/Topics/Obsidian Plugins",
            "keywords": ["obsidian", "plugin", "dataview", "vault"],
        },
    ]
    (agent_os / "topics.json").write_text(json.dumps(hubs), encoding="utf-8")
    return agent_os / "topics.json"


def test_match_returns_empty_when_path_is_none():
    assert match_topic_hubs(None, "anything", [], "body") == []


def test_match_returns_empty_when_file_missing(tmp_path: Path):
    missing = tmp_path / "nope.json"
    assert match_topic_hubs(missing, "title", [], "body") == []


def test_match_scores_by_keyword_hits_and_caps_at_three(topics_json: Path):
    title = "Building LLM Agents with Rust FFI"
    tags = ["agent", "rust"]
    body = "This note covers tool-use orchestration in pyo3."
    hubs = match_topic_hubs(topics_json, title, tags, body)
    assert len(hubs) <= 3
    # llm-agents should win: agent+llm+tool-use hit; rust-ffi second: rust+pyo3
    slugs = [h["slug"] for h in hubs]
    assert slugs[0] == "llm-agents"
    assert "rust-ffi" in slugs


def test_match_ignores_case_and_partial_words(topics_json: Path):
    hubs = match_topic_hubs(topics_json, "OBSIDIAN plugin guide", [], "")
    assert any(h["slug"] == "obsidian-plugins" for h in hubs)


def test_match_short_keywords_need_whole_words(tmp_path: Path):
    path = tmp_path / "topics.json"
    hubs = [{"slug": "web3", "title": "Web3", "path": "AgentOS/Topics/Web3.md", "keywords": ["WP", "opin", "KOL"]}]
    path.write_text(json.dumps(hubs), encoding="utf-8")
    body = "An opinion piece hosted on /wp-content/ about kolkata."
    assert match_topic_hubs(path, "Weekly opinions", [], body) == []
    assert match_topic_hubs(path, "Token WP draft", [], "")[0]["slug"] == "web3"


def test_match_returns_empty_on_invalid_json(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert match_topic_hubs(bad, "t", [], "b") == []


def test_scan_excludes_generated_folders(tmp_path: Path):
    """scan_vault must skip AgentOS/ and Claude Work/ folders."""
    vault = tmp_path / "Vault"
    vault.mkdir()
    (vault / "RealFolder").mkdir()
    (vault / "AgentOS").mkdir()
    (vault / "Claude Work").mkdir()
    (vault / ".hidden").mkdir()
    # Put a note in each so we can verify they're ignored
    for folder in ("RealFolder", "AgentOS", "Claude Work"):
        (vault / folder / "note.md").write_text("---\ntags: [x]\n---\n# Note\n", encoding="utf-8")
    result = scan_vault(vault)
    folders = result.get("folders", [])
    assert "RealFolder" in folders
    assert "AgentOS" not in folders
    assert "Claude Work" not in folders
    assert ".hidden" not in folders