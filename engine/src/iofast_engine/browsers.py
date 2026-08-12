"""Out-of-band browser-component delivery (PLAN.md "Browser delivery (LOCKED)").

The frozen sidecar ships static-only: playwright/patchright node DRIVERS are
stripped from the bundle (packaging/iofast.spec) because they cannot live
inside a signed read-only .app. This module downloads them on demand into app
support, pinned by version and SHA-256 (browser_pins.py — PyPI's own digests,
refuse-on-mismatch), then points both packages' driver resolution at the
downloaded copies. Chromium itself is fetched afterwards by each driver's own
`install chromium`, into PLAYWRIGHT_BROWSERS_PATH.

Layout under support_dir():
    driver/<package>-<version>/   node + package/cli.js (the wheel's driver/ tree)
    browsers/                     chromium builds (PLAYWRIGHT_BROWSERS_PATH)
"""

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from .browser_pins import PINS

DOWNLOAD_TIMEOUT = 60  # seconds without a byte, not total
CHUNK = 1 << 16


class IntegrityError(Exception):
    """Downloaded payload does not match its pinned SHA-256 — refused."""


def support_dir() -> str:
    """App-support root. IOFAST_SUPPORT_DIR overrides for tests and the gate."""
    override = os.environ.get("IOFAST_SUPPORT_DIR")
    if override:
        return override
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/iofast")
    return os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "iofast"
    )


def default_storage_env():
    """Browsers and caches live under app support — the frozen bundle dir is
    read-only in a signed .app. Explicit spawn-time env still wins (setdefault)."""
    support = support_dir()
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(support, "browsers"))
    os.environ.setdefault("IOFAST_CACHE_DIR", os.path.join(support, "cache"))


def driver_dir(package: str, pins=None) -> str:
    version = (pins or PINS)[package]["version"]
    return os.path.join(support_dir(), "driver", f"{package}-{version}")


def _driver_paths(package: str, pins=None):
    base = driver_dir(package, pins)
    return os.path.join(base, "node"), os.path.join(base, "package", "cli.js")


def drivers_installed(pins=None) -> bool:
    return all(
        os.path.exists(path)
        for package in (pins or PINS)
        for path in _driver_paths(package, pins)
    )


def status() -> dict:
    chromium_dir = os.path.join(support_dir(), "browsers")
    chromium = os.path.isdir(chromium_dir) and any(
        name.startswith("chromium") for name in os.listdir(chromium_dir)
    )
    drivers = drivers_installed()
    return {
        "installed": drivers and chromium,
        "drivers": drivers,
        "chromium": chromium,
        "path": support_dir(),
    }


def activate():
    """Point playwright/patchright at the downloaded drivers when the package's
    own driver is absent (the frozen bundle strips it). No-op in a dev venv.

    _transport imports compute_driver_executable by name at module import time,
    so both the _driver definition and _transport's reference are replaced.
    """
    if not drivers_installed():
        return
    for package in PINS:
        node, cli = _driver_paths(package)

        def resolve(node=node, cli=cli):
            return (os.environ.get("PLAYWRIGHT_NODEJS_PATH", node), cli)

        try:
            driver_mod = __import__(f"{package}._impl._driver", fromlist=["_"])
            transport_mod = __import__(f"{package}._impl._transport", fromlist=["_"])
        except ImportError:
            continue
        packaged_cli = driver_mod.compute_driver_executable()[1]
        if os.path.exists(packaged_cli) and packaged_cli != cli:
            continue  # dev venv: the wheel's own driver is present — leave it
        driver_mod.compute_driver_executable = resolve
        transport_mod.compute_driver_executable = resolve


def _pick_wheel(pin: dict) -> dict:
    if sys.platform != "darwin":
        raise RuntimeError(f"browser components: unsupported platform {sys.platform}")
    machine = platform.machine()
    wheels = pin["wheels"]
    if machine not in wheels:
        raise RuntimeError(f"browser components: no pinned wheel for {machine}")
    return wheels[machine]


def _download_verified(url: str, sha256: str, dest: str, size: int, progress):
    """Stream url to dest, refusing (and deleting) on digest mismatch."""
    digest = hashlib.sha256()
    done = 0
    last_pct = -1
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response, open(
            dest, "wb"
        ) as handle:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                handle.write(chunk)
                done += len(chunk)
                pct = min(99, done * 100 // max(size, 1))
                if pct != last_pct:
                    last_pct = pct
                    progress(pct)
    except BaseException:
        if os.path.exists(dest):
            os.remove(dest)
        raise
    if digest.hexdigest() != sha256:
        os.remove(dest)
        raise IntegrityError(
            f"checksum mismatch for {os.path.basename(url)}: "
            f"expected {sha256}, got {digest.hexdigest()}"
        )
    progress(100)


def _extract_driver(wheel_path: str, package: str, pins):
    """Unpack the wheel's <package>/driver/ tree into driver_dir, atomically
    (staging dir + os.replace — a crash never leaves a half-installed driver)."""
    prefix = f"{package}/driver/"
    final = driver_dir(package, pins)
    parent = os.path.dirname(final)
    os.makedirs(parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".extract-", dir=parent)
    try:
        with zipfile.ZipFile(wheel_path) as wheel:
            members = [m for m in wheel.infolist() if m.filename.startswith(prefix)]
            if not members:
                raise IntegrityError(f"{os.path.basename(wheel_path)}: no {prefix} tree")
            for member in members:
                relative = member.filename[len(prefix):]
                if not relative or relative.endswith("/"):
                    continue
                target = os.path.join(staging, relative)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with wheel.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                # zipfile drops unix permissions on extract; restore exec bits
                mode = (member.external_attr >> 16) & 0o777
                if mode:
                    os.chmod(target, mode)
        if os.path.exists(final):
            shutil.rmtree(final)
        os.replace(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _install_chromium(package: str, pins, runner):
    """Each driver fetches its own pinned chromium into PLAYWRIGHT_BROWSERS_PATH."""
    node, cli = _driver_paths(package, pins)
    env = dict(os.environ)
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(support_dir(), "browsers"))
    result = runner([node, cli, "install", "chromium"], env=env, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError(
            f"{package} chromium install failed (exit {result.returncode}): "
            + " / ".join(tail)
        )


def install(emit=lambda event: None, runner=subprocess.run, pins=None):
    """Download + verify + extract both drivers, then chromium for each.

    Emits {"type": "browser_install", "component", "stage", "pct"?, "message"}
    events. Raises IntegrityError on checksum mismatch (nothing kept on disk).
    """
    pins = pins or PINS

    def event(component, stage, message, **extra):
        emit(
            {
                "type": "browser_install",
                "component": component,
                "stage": stage,
                "message": message,
                **extra,
            }
        )

    for package, pin in pins.items():
        wheel = _pick_wheel(pin)
        base = os.path.join(support_dir(), "driver")
        os.makedirs(base, exist_ok=True)
        wheel_path = os.path.join(base, f".{package}.whl.part")
        event(package, "download", f"downloading {package} driver {pin['version']}…")
        try:
            _download_verified(
                wheel["url"],
                wheel["sha256"],
                wheel_path,
                wheel["size"],
                lambda pct, package=package, pin=pin: event(
                    package,
                    "download",
                    f"downloading {package} driver {pin['version']}… {pct}%",
                    pct=pct,
                ),
            )
            event(package, "extract", f"unpacking {package} driver…")
            _extract_driver(wheel_path, package, pins)
        finally:
            if os.path.exists(wheel_path):
                os.remove(wheel_path)
        event(package, "chromium", f"installing chromium via {package}…")
        _install_chromium(package, pins, runner)
        event(package, "done", f"{package} components ready")
    activate()
    return status()
