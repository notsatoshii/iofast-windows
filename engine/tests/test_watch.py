"""Offline tests for watch list feature.

Uses fixture RSS feeds and fake listings — never hits the live web.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def watch_store_tmp(tmp_path):
    """Redirect watch store to a temp dir so tests don't touch real APPDATA."""
    store_dir = tmp_path / "iofast"
    store_dir.mkdir()
    watch_file = store_dir / "watch.json"

    def fake_app_support():
        return store_dir

    with patch("iofast_engine.watch.store._app_support_dir", side_effect=fake_app_support):
        yield store_dir


def test_add_and_list(watch_store_tmp):
    from iofast_engine.watch.store import add, list_entries

    entry = add("https://www.youtube.com/@TestChannel", every="daily", file=True, limit=5)
    assert entry["kind"] == "youtube"
    assert entry["every"] == "daily"
    assert entry["file"] is True
    assert entry["limit"] == 5
    assert entry["last_run"] is None

    entries = list_entries()
    assert len(entries) == 1
    assert entries[0]["id"] == entry["id"]


def test_add_dedupe(watch_store_tmp):
    from iofast_engine.watch.store import add, list_entries

    e1 = add("https://github.com/test/repo")
    e2 = add("https://github.com/test/repo")
    assert e1["id"] == e2["id"]
    assert len(list_entries()) == 1


def test_remove(watch_store_tmp):
    from iofast_engine.watch.store import add, list_entries, remove

    entry = add("https://example.com/blog")
    assert remove(entry["id"][:6]) is True
    assert len(list_entries()) == 0
    assert remove("nonexistent") is False


def test_detect_kind(watch_store_tmp):
    from iofast_engine.watch.store import _detect_kind

    assert _detect_kind("https://www.youtube.com/@channel") == "youtube"
    assert _detect_kind("https://youtube.com/playlist?list=abc") == "youtube"
    assert _detect_kind("https://github.com/owner/repo") == "github"
    assert _detect_kind("https://instagram.com/user") == "instagram"
    assert _detect_kind("https://x.com/user") == "x"
    assert _detect_kind("https://example.com/page") == "web"


def test_due_entries(watch_store_tmp):
    from datetime import datetime, timedelta, timezone
    from iofast_engine.watch.store import add, get_due_entries, update_entry

    e1 = add("https://example.com/a", every="daily")
    e2 = add("https://example.com/b", every="weekly")

    # Both are due (never run)
    due = get_due_entries(if_due=True)
    assert len(due) == 2

    # Mark e1 as just run
    update_entry(e1["id"], last_run=datetime.now(timezone.utc).isoformat())
    due = get_due_entries(if_due=True)
    assert len(due) == 1
    assert due[0]["id"] == e2["id"]

    # Without if_due, return all
    all_entries = get_due_entries(if_due=False)
    assert len(all_entries) == 2


def test_check_web_rss_baseline(watch_store_tmp):
    """First run of RSS checker records baseline, returns nothing."""
    from iofast_engine.watch.checker import check_web

    rss_xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><guid>a1</guid><title>Post A</title><link>http://ex.com/a</link></item>
  <item><guid>b2</guid><title>Post B</title><link>http://ex.com/b</link></item>
</channel></rss>"""

    entry = {"url": "http://example.com", "limit": 10, "last_seen": {}}

    with patch("iofast_engine.watch.checker._fetch_text") as mock_fetch:
        # First call returns page HTML with RSS link, second returns RSS
        mock_fetch.side_effect = [
            '<link rel="alternate" type="application/rss+xml" href="/feed.xml">',
            rss_xml,
        ]
        items = check_web(entry)

    # First run: baseline recorded, no items returned
    assert items == []
    assert entry["last_seen"]["initialized"] is True
    assert len(entry["last_seen"]["feed_guids"]) == 2


def test_check_web_rss_new_items(watch_store_tmp):
    """Second run detects new RSS items via _check_feed directly."""
    from iofast_engine.watch.checker import _check_feed

    rss_xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><guid>a1</guid><title>Post A</title><link>http://ex.com/a</link></item>
  <item><guid>b2</guid><title>Post B</title><link>http://ex.com/b</link></item>
  <item><guid>c3</guid><title>Post C</title><link>http://ex.com/c</link></item>
</channel></rss>"""

    last_seen = {
        "feed_guids": ["a1", "b2"],
        "feed_url": "http://example.com/feed.xml",
        "initialized": True,
    }
    entry = {"url": "http://example.com", "limit": 10, "last_seen": last_seen}

    with patch("iofast_engine.watch.checker._fetch_text", return_value=rss_xml):
        items = _check_feed("http://example.com/feed.xml", last_seen, entry, 10)

    assert len(items) == 1
    assert items[0]["id"].startswith("feed:c3")


def test_check_github_baseline(watch_store_tmp):
    """First GitHub check records baseline."""
    from iofast_engine.watch.checker import check_github

    releases = [{"tag_name": "v1.0", "name": "Release 1.0", "html_url": "http://gh.com/r"}]
    readme = {"sha": "abc123", "content": ""}

    entry = {"url": "https://github.com/test/repo", "limit": 10, "last_seen": {}}

    with patch("iofast_engine.watch.checker._fetch_json") as mock_fetch:
        mock_fetch.side_effect = [releases, readme]
        items = check_github(entry)

    assert items == []
    assert entry["last_seen"]["initialized"] is True
    assert "v1.0" in entry["last_seen"]["release_tags"]


def test_check_social_requires_signin(watch_store_tmp):
    """Social checker returns empty when not signed in."""
    from iofast_engine.watch.checker import check_social

    events = []
    entry = {"kind": "instagram", "url": "https://instagram.com/user", "last_seen": {}}

    with patch("iofast_engine.sources.social_rpc.status", return_value={"instagram": False}):
        items = check_social(entry, emit=lambda e: events.append(e))

    assert items == []
    assert any("sign-in" in e.get("message", "") for e in events)


def test_cli_watch_add_list(watch_store_tmp):
    """CLI watch add + list roundtrip."""
    from iofast_engine.cli import main

    # Add
    rc = main(["watch", "add", "https://example.com/test"])
    assert rc == 0

    # List
    rc = main(["watch", "list"])
    assert rc == 0


def test_cli_watch_remove(watch_store_tmp):
    from iofast_engine.cli import main

    main(["watch", "add", "https://example.com/rm-test"])
    from iofast_engine.watch.store import list_entries
    entry = list_entries()[0]

    rc = main(["watch", "remove", entry["id"][:6]])
    assert rc == 0
    assert len(list_entries()) == 0