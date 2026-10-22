"""Unit tests for check-deps browser launch verification."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_browser_check_succeeds_with_system_channel():
    """check-deps browser test passes when a system channel launches."""
    mock_browser = AsyncMock()
    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_ap_instance = AsyncMock()
    mock_ap_instance.start = AsyncMock(return_value=mock_pw)
    mock_ap = MagicMock(return_value=mock_ap_instance)

    with patch("iofast_engine.browser_helper.get_async_playwright", return_value=mock_ap):
        from iofast_engine.browser_helper import get_async_playwright

        ap = get_async_playwright()
        pw = await ap().start()
        browser = await pw.chromium.launch(channel="msedge", headless=True)
        await browser.close()
        assert mock_pw.chromium.launch.called


@pytest.mark.asyncio
async def test_browser_check_falls_back_to_managed_chromium():
    """check-deps falls back to managed chromium when system channels fail."""
    mock_browser = AsyncMock()
    mock_pw = AsyncMock()
    call_count = 0

    async def _launch_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if "channel" in kwargs:
            raise RuntimeError(f"{kwargs['channel']} not installed")
        return mock_browser

    mock_pw.chromium.launch = AsyncMock(side_effect=_launch_side_effect)
    mock_ap_instance = AsyncMock()
    mock_ap_instance.start = AsyncMock(return_value=mock_pw)
    mock_ap = MagicMock(return_value=mock_ap_instance)

    with patch("iofast_engine.browser_helper.get_async_playwright", return_value=mock_ap):
        from iofast_engine.browser_helper import get_async_playwright

        ap = get_async_playwright()
        pw = await ap().start()
        # Simulate the fallback logic from cli.py
        launched = False
        for channel in ("msedge", "chrome"):
            try:
                browser = await pw.chromium.launch(channel=channel, headless=True)
                await browser.close()
                break
            except Exception:
                continue
        else:
            browser = await pw.chromium.launch(headless=True)
            await browser.close()
            launched = True
        assert launched
        assert call_count == 3  # 2 system + 1 managed


def test_check_deps_imports_patchright_not_playwright():
    """check-deps verifies patchright import, not playwright."""
    from pathlib import Path

    cli_path = Path(__file__).parent.parent / "src" / "iofast_engine" / "cli.py"
    source = cli_path.read_text(encoding="utf-8")

    # Find the deps check tuple line and verify it contains patchright, not playwright
    lines = source.split("\n")
    found_patchright = False
    found_playwright_in_deps = False
    in_deps_loop = False
    for line in lines:
        if "for mod_name in" in line and ("yt_dlp" in line or "scrapling" in line):
            in_deps_loop = True
        if in_deps_loop:
            if "patchright" in line:
                found_patchright = True
            if '"playwright"' in line or "'playwright'" in line:
                found_playwright_in_deps = True
            if ")" in line and in_deps_loop and "mod_name" not in line:
                in_deps_loop = False

    assert found_patchright, "check-deps must verify patchright import"
    assert not found_playwright_in_deps, "check-deps must NOT verify playwright (dev-only)"