"""IOFAST_PROXY threads into every fetch tier — and stays absent when unset.

Stubs the three Scrapling session classes with recorders so the test pins OUR
env->kwarg logic (spider.configure_sessions), not Scrapling's proxy behaviour.
configure_sessions imports the classes from scrapling.fetchers at call time, so
patching the module attributes is enough.
"""

import scrapling.fetchers as fetchers

from iofast_engine.spider import DocSpider

TIER_CLASSES = {
    "static": "FetcherSession",
    "dynamic": "AsyncDynamicSession",
    "stealth": "AsyncStealthySession",
}


class _Manager:
    def __init__(self):
        self.sessions = {}

    def add(self, name, session, **_kw):
        self.sessions[name] = session


class _Spider:
    """configure_sessions only reads self.browsers."""

    browsers = True


def _record_sessions(monkeypatch):
    seen = {}

    def recorder(tier):
        class Rec:
            def __init__(self, **kw):
                seen[tier] = kw

        return Rec

    for tier, cls_name in TIER_CLASSES.items():
        monkeypatch.setattr(fetchers, cls_name, recorder(tier))
    return seen


def test_proxy_env_threads_into_every_tier(monkeypatch):
    seen = _record_sessions(monkeypatch)
    monkeypatch.setenv("IOFAST_PROXY", "http://u:p@host:9")

    DocSpider.configure_sessions(_Spider(), _Manager())

    for tier in TIER_CLASSES:
        assert seen[tier].get("proxy") == "http://u:p@host:9", tier


def test_no_proxy_env_means_no_proxy_kwarg(monkeypatch):
    seen = _record_sessions(monkeypatch)
    monkeypatch.delenv("IOFAST_PROXY", raising=False)

    DocSpider.configure_sessions(_Spider(), _Manager())

    for tier in TIER_CLASSES:
        assert "proxy" not in seen[tier], tier


def test_blank_proxy_env_is_treated_as_unset(monkeypatch):
    # A clear-then-empty keychain read must not pass proxy="" to the fetchers.
    seen = _record_sessions(monkeypatch)
    monkeypatch.setenv("IOFAST_PROXY", "")

    DocSpider.configure_sessions(_Spider(), _Manager())

    for tier in TIER_CLASSES:
        assert "proxy" not in seen[tier], tier
