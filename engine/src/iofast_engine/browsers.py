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
    components.json               install receipt (per package: version, digests)
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

from .browser_pins import PINS

DOWNLOAD_TIMEOUT = 60  # seconds without a byte, not total
DOWNLOAD_ATTEMPTS = 3  # the digest check makes a blind retry safe
CHROMIUM_TIMEOUT = 900  # seconds for one driver's `install chromium`
DISK_NEED = 2 * 1024**3  # drivers + chromium + staging headroom
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


def _receipt_path() -> str:
    return os.path.join(support_dir(), "components.json")


def _load_receipt() -> dict:
    try:
        with open(_receipt_path(), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _write_receipt(receipt: dict):
    path = _receipt_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2)
    os.replace(tmp, path)


def _file_digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_package(package: str, pins, wheel_sha256: str):
    """Write the package's receipt entry: pinned version, wheel digest, and the
    on-disk digest+size of the driver's load-bearing files."""
    base = driver_dir(package, pins)
    node, cli = _driver_paths(package, pins)
    entry = {
        "version": (pins or PINS)[package]["version"],
        "wheel_sha256": wheel_sha256,
        "installed_at": int(time.time()),
        "files": {
            os.path.relpath(path, base): {
                "sha256": _file_digest(path),
                "size": os.path.getsize(path),
            }
            for path in (node, cli)
        },
    }
    registry = os.path.join(base, "package", "browsers.json")
    try:
        with open(registry, encoding="utf-8") as handle:
            data = json.load(handle)
        entry["chromium_revision"] = next(
            b["revision"] for b in data.get("browsers", []) if b.get("name") == "chromium"
        )
    except (OSError, ValueError, StopIteration):
        pass  # fixture wheels carry no registry; revision is informational
    receipt = _load_receipt()
    receipt[package] = entry
    _write_receipt(receipt)


def _package_reason(package: str, pins=None, receipt=None):
    """Full digest check against the receipt: None when healthy, else the
    reason. Used by verify() and by install()'s skip decision, so a re-run of
    install repairs anything verify would flag."""
    receipt = _load_receipt() if receipt is None else receipt
    entry = receipt.get(package)
    if not entry or entry.get("version") != (pins or PINS)[package]["version"]:
        return "no receipt for the pinned version"
    base = driver_dir(package, pins)
    for rel, meta in sorted((entry.get("files") or {}).items()):
        path = os.path.join(base, rel)
        if not os.path.exists(path):
            return f"missing {rel}"
        if _file_digest(path) != meta.get("sha256"):
            return f"digest mismatch on {rel}"
    return None


def _package_ok(package: str, pins=None, receipt=None) -> bool:
    """Cheap per-scrape check: receipt entry for the pinned version exists and
    every recorded file is present at its recorded size (full digests are the
    `verify` action's job — hashing ~250MB per scrape would be absurd)."""
    receipt = _load_receipt() if receipt is None else receipt
    entry = receipt.get(package)
    if not entry or entry.get("version") != (pins or PINS)[package]["version"]:
        return False
    base = driver_dir(package, pins)
    files = entry.get("files") or {}
    if not files:
        return False
    return all(
        os.path.exists(os.path.join(base, rel))
        and os.path.getsize(os.path.join(base, rel)) == meta.get("size")
        for rel, meta in files.items()
    )


def drivers_installed(pins=None) -> bool:
    receipt = _load_receipt()
    return all(_package_ok(package, pins, receipt) for package in (pins or PINS))


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


def _urlopen(url: str):
    """IOFAST_PROXY routes the driver download like every other fetch tier."""
    proxy = os.environ.get("IOFAST_PROXY")
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        return opener.open(url, timeout=DOWNLOAD_TIMEOUT)
    return urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT)


def _download_verified(url: str, sha256: str, dest: str, size: int, progress):
    """Stream url to dest, refusing (and deleting) on digest mismatch."""
    digest = hashlib.sha256()
    done = 0
    last_pct = -1
    try:
        with _urlopen(url) as response, open(
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


def _download_with_retry(url, sha256, dest, size, progress, on_retry):
    """Transient network failures and corrupt payloads both retry (the digest
    check makes a blind retry safe); the last attempt's error propagates."""
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            return _download_verified(url, sha256, dest, size, progress)
        except (OSError, IntegrityError) as error:
            if attempt == DOWNLOAD_ATTEMPTS:
                raise
            on_retry(attempt, error)
            time.sleep(2 ** (attempt - 1))


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


def _stream_runner(cmd, env=None, on_line=lambda line: None, timeout=CHROMIUM_TIMEOUT, **_):
    """Default runner: stream the child's merged output line by line (chromium's
    download progress was dark for 2/3 of the wait) and kill it at timeout."""
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines = []

    def read():
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            on_line(line)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError(f"{os.path.basename(cmd[0])} timed out after {timeout}s")
    reader.join(timeout=5)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout="\n".join(lines), stderr="")


def _install_chromium(package: str, pins, runner, on_line=lambda line: None):
    """Each driver fetches its own pinned chromium into PLAYWRIGHT_BROWSERS_PATH."""
    node, cli = _driver_paths(package, pins)
    env = dict(os.environ)
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(support_dir(), "browsers"))
    proxy = os.environ.get("IOFAST_PROXY")
    if proxy:
        env.setdefault("HTTPS_PROXY", proxy)
        env.setdefault("HTTP_PROXY", proxy)
    result = runner(
        [node, cli, "install", "chromium"],
        env=env,
        on_line=on_line,
        timeout=CHROMIUM_TIMEOUT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError(
            f"{package} chromium install failed (exit {result.returncode}): "
            + " / ".join(tail)
        )


def install(emit=lambda event: None, runner=_stream_runner, pins=None):
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

    base = os.path.join(support_dir(), "driver")
    os.makedirs(base, exist_ok=True)
    free = shutil.disk_usage(support_dir()).free
    if free < DISK_NEED:
        raise RuntimeError(
            f"insufficient disk space: {free // 1024**2}MB free under "
            f"{support_dir()}, need ~{DISK_NEED // 1024**2}MB"
        )

    for package, pin in pins.items():
        if _package_reason(package, pins) is None:
            event(package, "skip", f"{package} driver {pin['version']} already installed")
        else:
            wheel = _pick_wheel(pin)
            wheel_path = os.path.join(base, f".{package}.whl.part")
            event(package, "download", f"downloading {package} driver {pin['version']}…")
            try:
                _download_with_retry(
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
                    lambda attempt, error, package=package: event(
                        package,
                        "download",
                        f"download attempt {attempt} failed ({error}); retrying…",
                    ),
                )
                event(package, "extract", f"unpacking {package} driver…")
                _extract_driver(wheel_path, package, pins)
            finally:
                if os.path.exists(wheel_path):
                    os.remove(wheel_path)
            _record_package(package, pins, wheel["sha256"])
        event(package, "chromium", f"installing chromium via {package}…")
        _install_chromium(
            package,
            pins,
            runner,
            on_line=lambda line, package=package: event(package, "chromium", line),
        )
        event(package, "done", f"{package} components ready")
    activate()
    return status()


def verify(repair=False, emit=lambda event: None, runner=_stream_runner, pins=None) -> dict:
    """Recompute on-disk digests against the install receipt (the cheap
    per-scrape check trusts sizes only — this is the honest one). With
    repair=True, bad packages are removed and reinstalled."""
    pins = pins or PINS
    receipt = _load_receipt()
    report = {}
    bad = {}
    for package in pins:
        reason = _package_reason(package, pins, receipt)
        report[package] = reason or "ok"
        if reason:
            bad[package] = reason
    result = {"ok": not bad, "packages": report, "status": status()}
    if bad and repair:
        for package in bad:
            shutil.rmtree(driver_dir(package, pins), ignore_errors=True)
            receipt.pop(package, None)
        _write_receipt(receipt)
        install(emit=emit, runner=runner, pins={p: pins[p] for p in bad})
        result = verify(repair=False, pins=pins)
        result["repaired"] = sorted(bad)
    return result
