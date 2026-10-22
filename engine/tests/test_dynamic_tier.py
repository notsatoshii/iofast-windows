"""Tests for the dynamic tier browser launch and error reporting."""
import pytest
from unittest.mock import patch, MagicMock


def test_fetch_tier_dynamic_calls_browser_helper():
    """The dynamic tier must call browser_helper.fetch_with_browser, not scrapling."""
    from iofast_engine.sources.web import WebSource

    fake_html = "<html><body><p>Hello from JS</p></body></html>"
    with patch(
        "iofast_engine.browser_helper.fetch_with_browser",
        return_value=(fake_html, "system msedge"),
    ):
        response, info = WebSource._fetch_tier("https://example.com", "dynamic")

    assert response is not None, "dynamic tier must return a response when browser succeeds"
    assert response.status == 200
    assert "Hello from JS" in response.text


def test_fetch_tier_dynamic_reports_error_not_none():
    """When the browser fails, _fetch_tier returns (None, error_string), not (None, None)."""
    from iofast_engine.sources.web import WebSource

    with patch(
        "iofast_engine.browser_helper.fetch_with_browser",
        return_value=(None, "error: ConnectionRefusedError: browser died"),
    ):
        response, info = WebSource._fetch_tier("https://example.com", "dynamic")

    assert response is None
    assert info is not None
    assert "error" in info.lower()
    assert "ConnectionRefusedError" in info


def test_fetch_tier_stealth_does_not_solve_cloudflare():
    """The stealth tier must NOT pass solve_cloudflare=True (Eric's rule)."""
    from pathlib import Path

    spider_path = Path(__file__).parent.parent / "src" / "iofast_engine" / "spider.py"
    source = spider_path.read_text(encoding="utf-8")

    assert "solve_cloudflare=False" in source, (
        "spider.py stealth session must have solve_cloudflare=False"
    )
    assert "solve_cloudflare=True" not in source, (
        "spider.py must not have solve_cloudflare=True anywhere"
    )


def test_browsers_available_checks_system_channels():
    """browsers_available() tries system Edge/Chrome before managed Chromium."""
    from iofast_engine.tiers import browsers_available

    mock_browser = MagicMock()
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser
    mock_sp = MagicMock()
    mock_sp.return_value.__enter__ = MagicMock(return_value=mock_pw)
    mock_sp.return_value.__exit__ = MagicMock(return_value=False)

    with patch("iofast_engine.browser_helper.get_sync_playwright", return_value=mock_sp):
        result = browsers_available()

    assert result is True
    first_call_kwargs = mock_pw.chromium.launch.call_args_list[0]
    assert "channel" in first_call_kwargs.kwargs or (
        first_call_kwargs.args and len(first_call_kwargs.args) > 0
    ), "first launch attempt must try a system channel"


def test_escalation_emits_tier_failure_reason():
    """When a tier fails, the escalation loop emits the failure reason."""
    from iofast_engine.sources.web import WebSource

    events = []

    def capture_event(event):
        events.append(event)

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.headers = {}
    mock_response.body = b"<html><body><p>thin</p></body></html>"
    mock_response.text = "<html><body><p>thin</p></body></html>"

    with patch.object(WebSource, "_fetch_tier") as mock_ft:
        mock_ft.side_effect = [
            (mock_response, "static"),
            (None, "error: TimeoutError: page took too long"),
            (None, "error: TimeoutError: page took too long"),
        ]
        source = WebSource()
        text, tier, how, status = source._fetch_with_escalation(
            "https://example.com", capture_event
        )

    warning_events = [e for e in events if e.get("type") == "warning"]
    assert any("TimeoutError" in e.get("message", "") for e in warning_events), (
        f"Expected timeout error in warning events, got: {warning_events}"
    )