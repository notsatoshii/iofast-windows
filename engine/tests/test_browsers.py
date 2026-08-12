"""Out-of-band browser-component delivery — offline, deterministic.

Downloads are file:// URLs to tiny fixture wheels built per test; chromium
install is a recorded stub runner. The must-reject cases come first: the
refuse-on-mismatch guard is only trusted once a mismatching payload is refused.
"""

import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile

import pytest

import iofast_engine.browsers as browsers
from iofast_engine.rpc import RpcServer


def make_wheel(path, package="playwright", with_driver=True):
    """Tiny fixture wheel: <package>/driver/node (exec bit) + package/cli.js."""
    with zipfile.ZipFile(path, "w") as wheel:
        if with_driver:
            node = zipfile.ZipInfo(f"{package}/driver/node")
            node.external_attr = 0o755 << 16
            wheel.writestr(node, "#!/bin/sh\nexit 0\n")
            wheel.writestr(f"{package}/driver/package/cli.js", "// cli")
        wheel.writestr(f"{package}/__init__.py", "")
    with open(path, "rb") as handle:
        payload = handle.read()
    return hashlib.sha256(payload).hexdigest(), len(payload)


def make_pins(tmp_path, packages=("playwright",), sha_override=None, with_driver=True):
    machine = __import__("platform").machine()
    pins = {}
    for package in packages:
        wheel_path = tmp_path / f"{package}-fixture.whl"
        sha, size = make_wheel(wheel_path, package, with_driver=with_driver)
        pins[package] = {
            "version": "0.0.1",
            "wheels": {
                machine: {
                    "url": wheel_path.as_uri(),
                    "sha256": sha_override or sha,
                    "size": size,
                }
            },
        }
    return pins


def ok_runner(calls):
    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return runner


@pytest.fixture
def support(tmp_path, monkeypatch):
    root = tmp_path / "support"
    monkeypatch.setenv("IOFAST_SUPPORT_DIR", str(root))
    return root


@pytest.fixture
def darwin(monkeypatch):
    """install() is mac-only by design; CI runs on linux — pin the platform."""
    monkeypatch.setattr(browsers.sys, "platform", "darwin")


# ---------- must-reject cases first ----------


def test_checksum_mismatch_refused_and_nothing_kept(tmp_path, support, darwin):
    pins = make_pins(tmp_path, sha_override="0" * 64)
    with pytest.raises(browsers.IntegrityError, match="checksum mismatch"):
        browsers.install(runner=ok_runner([]), pins=pins)
    driver_root = support / "driver"
    leftovers = list(driver_root.iterdir()) if driver_root.exists() else []
    assert leftovers == [], f"mismatching download left {leftovers}"
    assert not browsers.drivers_installed(pins)


def test_wheel_without_driver_tree_refused(tmp_path, support, darwin):
    pins = make_pins(tmp_path, with_driver=False)
    with pytest.raises(browsers.IntegrityError, match="no playwright/driver/ tree"):
        browsers.install(runner=ok_runner([]), pins=pins)
    assert not browsers.drivers_installed(pins)


def test_unsupported_platform_refused(tmp_path, support, monkeypatch):
    pins = make_pins(tmp_path)
    monkeypatch.setattr(browsers.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="unsupported platform"):
        browsers.install(runner=ok_runner([]), pins=pins)


def test_chromium_install_failure_raises(tmp_path, support, darwin):
    pins = make_pins(tmp_path)

    def failing(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 7, stdout="", stderr="boom\nno disk")

    with pytest.raises(RuntimeError, match=r"exit 7.*no disk"):
        browsers.install(runner=failing, pins=pins)
    # drivers were extracted before chromium failed — retry only re-fetches chromium
    assert browsers.drivers_installed(pins)


# ---------- must-pass ----------


def test_install_extracts_drivers_and_installs_chromium(tmp_path, support, darwin):
    pins = make_pins(tmp_path, packages=("playwright", "patchright"))
    calls = []
    events = []
    browsers.install(emit=events.append, runner=ok_runner(calls), pins=pins)

    assert browsers.drivers_installed(pins)
    for package in ("playwright", "patchright"):
        base = support / "driver" / f"{package}-0.0.1"
        node, cli = base / "node", base / "package" / "cli.js"
        assert node.exists() and cli.exists()
        assert os.access(node, os.X_OK), "exec bit lost on extract"
    # one chromium install per driver, pointed at the shared browsers dir
    assert [cmd[2:] for cmd, _ in calls] == [["install", "chromium"], ["install", "chromium"]]
    for cmd, kwargs in calls:
        assert cmd[0].endswith("/node") and cmd[1].endswith("/cli.js")
        assert kwargs["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(support / "browsers")
    stages = [(event["component"], event["stage"]) for event in events]
    for package in ("playwright", "patchright"):
        for stage in ("download", "extract", "chromium", "done"):
            assert (package, stage) in stages
    assert all(event["type"] == "browser_install" for event in events)
    # no .part temp files left behind
    assert not list((support / "driver").glob("*.part"))


def test_status_reflects_components(tmp_path, support):
    assert browsers.status() == {
        "installed": False,
        "drivers": False,
        "chromium": False,
        "path": str(support),
    }
    # fake chromium build dir; drivers still absent -> not installed
    (support / "browsers" / "chromium-1234").mkdir(parents=True)
    state = browsers.status()
    assert state["chromium"] and not state["drivers"] and not state["installed"]


def test_activate_points_playwright_at_downloaded_driver(support, monkeypatch):
    import playwright._impl._driver as driver_mod
    import playwright._impl._transport as transport_mod

    for package in browsers.PINS:
        base = browsers.driver_dir(package)
        os.makedirs(os.path.join(base, "package"), exist_ok=True)
        with open(os.path.join(base, "node"), "w") as handle:
            handle.write("")
        with open(os.path.join(base, "package", "cli.js"), "w") as handle:
            handle.write("")
    originals = (driver_mod.compute_driver_executable, transport_mod.compute_driver_executable)
    # simulate the frozen bundle: the package's own driver cli.js is absent
    monkeypatch.setattr(browsers.os.path, "exists", lambda p: not p.endswith("cli.js") or str(browsers.support_dir()) in p)
    try:
        browsers.activate()
        node, cli = driver_mod.compute_driver_executable()
        expected = browsers.driver_dir("playwright")
        assert node == os.path.join(expected, "node")
        assert cli == os.path.join(expected, "package", "cli.js")
        assert transport_mod.compute_driver_executable()[1] == cli
    finally:
        driver_mod.compute_driver_executable, transport_mod.compute_driver_executable = originals


def test_activate_noop_in_dev_venv(support):
    """The venv wheel ships its own driver — activate must leave it alone."""
    import playwright._impl._driver as driver_mod

    for package in browsers.PINS:
        base = browsers.driver_dir(package)
        os.makedirs(os.path.join(base, "package"), exist_ok=True)
        for name in ("node", os.path.join("package", "cli.js")):
            with open(os.path.join(base, name), "w") as handle:
                handle.write("")
    original = driver_mod.compute_driver_executable
    try:
        browsers.activate()
        assert driver_mod.compute_driver_executable is original
    finally:
        driver_mod.compute_driver_executable = original


def test_browsers_available_false_when_frozen_without_components(support, monkeypatch):
    from iofast_engine import tiers

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert tiers.browsers_available() is False


# ---------- RPC wiring ----------


def test_rpc_browser_status_and_install(monkeypatch):
    out = io.StringIO()
    server = RpcServer(stdin=None, stdout=out, exit_fn=lambda code: None)
    monkeypatch.setattr(browsers, "status", lambda: {"installed": False, "drivers": False, "chromium": False, "path": "/x"})
    server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "browser_status"})

    def canned_install(emit=lambda e: None, **kwargs):
        emit({"type": "browser_install", "component": "playwright", "stage": "download", "message": "…"})
        return {"installed": True, "drivers": True, "chromium": True, "path": "/x"}

    monkeypatch.setattr(browsers, "install", canned_install)
    server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "install_browsers"})
    thread = server.threads.get("browsers")
    thread.join(timeout=5)
    assert not thread.is_alive()

    messages = [json.loads(line) for line in out.getvalue().splitlines()]
    assert messages[0] == {"jsonrpc": "2.0", "id": 1, "result": {"installed": False, "drivers": False, "chromium": False, "path": "/x"}}
    event = next(m for m in messages if m.get("method") == "event")
    assert event["params"]["type"] == "browser_install" and event["params"]["rid"] == 2
    final = next(m for m in messages if m.get("id") == 2)
    assert final["result"]["installed"] is True
