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


def seed_stub_drivers(packages=None, record=True):
    """Create on-disk driver stubs (and, by default, a matching receipt)."""
    for package in packages or browsers.PINS:
        base = browsers.driver_dir(package)
        os.makedirs(os.path.join(base, "package"), exist_ok=True)
        for name in ("node", os.path.join("package", "cli.js")):
            with open(os.path.join(base, name), "w") as handle:
                handle.write(f"stub {name}\n")
        if record:
            browsers._record_package(package, None, "0" * 64)


@pytest.fixture
def support(tmp_path, monkeypatch):
    root = tmp_path / "support"
    monkeypatch.setenv("IOFAST_SUPPORT_DIR", str(root))
    return root


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch):
    """Download retry backoff must not slow the suite down."""
    monkeypatch.setattr(browsers.time, "sleep", lambda seconds: None)


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

    seed_stub_drivers()
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

    seed_stub_drivers()
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


def test_stub_files_without_receipt_not_installed(support):
    """Bare on-disk stubs (no receipt) must not report installed — zero-byte
    stubs reported installed:true before the receipt existed."""
    seed_stub_drivers(record=False)
    assert not browsers.drivers_installed()
    assert browsers.status()["drivers"] is False


def test_truncated_file_fails_cheap_check(tmp_path, support, darwin):
    pins = make_pins(tmp_path)
    browsers.install(runner=ok_runner([]), pins=pins)
    assert browsers.drivers_installed(pins)
    node = os.path.join(browsers.driver_dir("playwright", pins), "node")
    with open(node, "w") as handle:
        handle.write("")  # truncate: size no longer matches the receipt
    assert not browsers.drivers_installed(pins)


def test_receipt_records_digests(tmp_path, support, darwin):
    pins = make_pins(tmp_path)
    browsers.install(runner=ok_runner([]), pins=pins)
    with open(support / "components.json") as handle:
        receipt = json.load(handle)
    entry = receipt["playwright"]
    assert entry["version"] == "0.0.1"
    assert entry["wheel_sha256"] == pins["playwright"]["wheels"][
        __import__("platform").machine()
    ]["sha256"]
    node = os.path.join(browsers.driver_dir("playwright", pins), "node")
    with open(node, "rb") as handle:
        payload = handle.read()
    assert entry["files"]["node"]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert entry["files"]["node"]["size"] == len(payload)


def test_second_install_skips_download(tmp_path, support, darwin):
    pins = make_pins(tmp_path)
    browsers.install(runner=ok_runner([]), pins=pins)
    # kill the source: a second download attempt would now fail loudly
    os.remove(tmp_path / "playwright-fixture.whl")
    calls = []
    events = []
    browsers.install(emit=events.append, runner=ok_runner(calls), pins=pins)
    assert ("playwright", "skip") in [(e["component"], e["stage"]) for e in events]
    assert browsers.drivers_installed(pins)
    # chromium install still runs (it is the authoritative no-op check)
    assert [cmd[2:] for cmd, _ in calls] == [["install", "chromium"]]


def test_download_retries_then_succeeds(tmp_path, support, darwin, monkeypatch):
    pins = make_pins(tmp_path)
    real = browsers._download_verified
    attempts = []

    def flaky(url, sha256, dest, size, progress):
        attempts.append(url)
        if len(attempts) < 3:
            raise OSError("connection reset")
        return real(url, sha256, dest, size, progress)

    monkeypatch.setattr(browsers, "_download_verified", flaky)
    events = []
    browsers.install(emit=events.append, runner=ok_runner([]), pins=pins)
    assert len(attempts) == 3
    retries = [e for e in events if "retrying" in e["message"]]
    assert len(retries) == 2
    assert browsers.drivers_installed(pins)


def test_download_retry_exhaustion_raises(tmp_path, support, darwin, monkeypatch):
    pins = make_pins(tmp_path)

    def always_down(url, sha256, dest, size, progress):
        raise OSError("no route to host")

    monkeypatch.setattr(browsers, "_download_verified", always_down)
    with pytest.raises(OSError, match="no route"):
        browsers.install(runner=ok_runner([]), pins=pins)


def test_disk_preflight_refuses(tmp_path, support, darwin, monkeypatch):
    pins = make_pins(tmp_path)
    usage = __import__("collections").namedtuple("usage", "total used free")
    monkeypatch.setattr(browsers.shutil, "disk_usage", lambda path: usage(1, 1, 10**6))
    with pytest.raises(RuntimeError, match="insufficient disk space"):
        browsers.install(runner=ok_runner([]), pins=pins)
    assert not browsers.drivers_installed(pins)


def test_proxy_reaches_downloader_and_chromium_child(tmp_path, support, darwin, monkeypatch):
    monkeypatch.setenv("IOFAST_PROXY", "http://127.0.0.1:9999")
    captured = {}
    real_build = browsers.urllib.request.build_opener

    def spy_build(*handlers):
        captured["handlers"] = handlers
        return real_build(*handlers)

    monkeypatch.setattr(browsers.urllib.request, "build_opener", spy_build)
    pins = make_pins(tmp_path)
    calls = []
    browsers.install(runner=ok_runner(calls), pins=pins)  # file:// bypasses the proxy
    handler = captured["handlers"][0]
    assert isinstance(handler, browsers.urllib.request.ProxyHandler)
    assert handler.proxies == {"http": "http://127.0.0.1:9999", "https": "http://127.0.0.1:9999"}
    env = calls[0][1]["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:9999"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:9999"


def test_chromium_output_streams_as_events(tmp_path, support, darwin):
    pins = make_pins(tmp_path)
    events = []

    def chatty(cmd, on_line=lambda line: None, **kwargs):
        on_line("Downloading Chromium 1208...")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    browsers.install(emit=events.append, runner=chatty, pins=pins)
    assert any(
        e["stage"] == "chromium" and e["message"] == "Downloading Chromium 1208..."
        for e in events
    )


def test_stream_runner_streams_and_times_out():
    lines = []
    result = browsers._stream_runner(
        ["/bin/sh", "-c", "echo one; echo two"], on_line=lines.append
    )
    assert lines == ["one", "two"] and result.returncode == 0
    with pytest.raises(RuntimeError, match="timed out"):
        browsers._stream_runner(["/bin/sh", "-c", "sleep 5"], timeout=0.2)


def test_verify_detects_same_size_tamper_and_repairs(tmp_path, support, darwin):
    pins = make_pins(tmp_path)
    browsers.install(runner=ok_runner([]), pins=pins)
    node = os.path.join(browsers.driver_dir("playwright", pins), "node")
    size = os.path.getsize(node)
    with open(node, "wb") as handle:
        handle.write(b"X" * size)  # same size: the cheap check must still pass
    assert browsers.drivers_installed(pins)
    report = browsers.verify(pins=pins)
    assert report["ok"] is False
    assert report["packages"]["playwright"] == "digest mismatch on node"
    repaired = browsers.verify(repair=True, runner=ok_runner([]), pins=pins)
    assert repaired["ok"] is True
    assert repaired["repaired"] == ["playwright"]
    assert browsers.verify(pins=pins)["ok"] is True


def test_install_rerun_repairs_same_size_tamper(tmp_path, support, darwin):
    """RPC repair path: install_browsers re-verifies digests per package, so a
    plain re-run heals what verify flags."""
    pins = make_pins(tmp_path)
    browsers.install(runner=ok_runner([]), pins=pins)
    node = os.path.join(browsers.driver_dir("playwright", pins), "node")
    size = os.path.getsize(node)
    with open(node, "wb") as handle:
        handle.write(b"X" * size)
    events = []
    browsers.install(emit=events.append, runner=ok_runner([]), pins=pins)
    assert ("playwright", "skip") not in [(e["component"], e["stage"]) for e in events]
    assert browsers.verify(pins=pins)["ok"] is True


def test_verify_without_receipt_reports_not_ok(support):
    report = browsers.verify()
    assert report["ok"] is False
    assert set(report["packages"]) == set(browsers.PINS)
    assert all(state == "no receipt for the pinned version" for state in report["packages"].values())


def test_pins_match_installed_packages():
    """Pin skew: browser_pins.py must track the venv (and thus the frozen
    bundle) — a version drift silently activates a mismatched driver."""
    import importlib.metadata

    for package, pin in browsers.PINS.items():
        assert pin["version"] == importlib.metadata.version(package), (
            f"{package} pinned {pin['version']} but installed "
            f"{importlib.metadata.version(package)} — regenerate browser_pins.py"
        )


# ---------- RPC wiring ----------


def test_rpc_verify_components(monkeypatch):
    out = io.StringIO()
    server = RpcServer(stdin=None, stdout=out, exit_fn=lambda code: None)
    monkeypatch.setattr(
        browsers, "verify", lambda **kwargs: {"ok": False, "packages": {"playwright": "missing node"}}
    )
    server.dispatch({"jsonrpc": "2.0", "id": 5, "method": "verify_components"})
    message = json.loads(out.getvalue().splitlines()[0])
    assert message["result"]["ok"] is False
    assert message["result"]["packages"]["playwright"] == "missing node"


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
