# -*- mode: python ; coding: utf-8 -*-
"""Frozen sidecar: STATIC TIER ONLY — playwright/patchright are excluded by
design (browsers download out-of-band into app support on first dynamic-tier
use; see PLAN.md "Browser delivery (LOCKED)" and docs/RPC.md).

Build:   .venv/bin/pyinstaller --noconfirm packaging/iofast.spec
Verify:  .venv/bin/python packaging/verify_frozen.py dist/iofast/iofast
The verify script IS the acceptance gate: it drives the frozen binary over
stdio JSON-RPC including a real scrape against a local fixture server.
"""

import os
import subprocess

from PyInstaller.utils.hooks import collect_data_files

# Build provenance: stamp the git SHA into the bundle so verify_frozen.py (and
# the app<->sidecar handshake) can prove which source this freeze came from.
# The file is gitignored; unfrozen runs ignore it and report "dev".
try:
    _rev = subprocess.run(
        ["git", "-C", SPECPATH, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    _sha = _rev.stdout.strip()
    _git_err = _rev.stderr.strip()
except OSError as exc:  # git binary absent (minimal container / sdist image)
    _sha, _git_err = "", str(exc)
if not _sha:
    # Fail loud at freeze time: a provenance-less bundle can never pass the
    # gate anyway, and this message beats a confusing gate failure later.
    raise SystemExit(
        f"iofast.spec: no git SHA available — build provenance requires a git checkout ({_git_err})"
    )
# Refuse a dirty freeze: HEAD's SHA on a tree with uncommitted bundle-input
# edits would stamp code that exists in no commit — the gate fails closed in
# THIS tree (it diffs the working tree), but a copied/symlinked dist would
# carry the stamp into a clean tree at the same SHA and pass there.
_dirty = subprocess.run(
    ["git", "-C", SPECPATH, "-c", "status.showUntrackedFiles=normal", "status",
     "--porcelain", "-uall", "--", "../src", "../packaging", "../pyproject.toml"],
    capture_output=True,
    text=True,
).stdout.strip()
if _dirty:
    raise SystemExit(
        "iofast.spec: refusing to freeze from a dirty tree — commit (or delete/ignore untracked) first:\n"
        + _dirty
    )
with open(os.path.join(SPECPATH, "..", "src", "iofast_engine", "_build.py"), "w") as fh:
    fh.write(f'BUILD_SHA = "{_sha}"\n')

hiddenimports = [
    "iofast_engine._build",
    # scrapling.fetchers resolves its classes through a string-keyed lazy-import
    # dict (__getattr__ + __import__), invisible to PyInstaller's modulegraph —
    # without these pins the fetch tiers are missing from the bundle. The chrome/
    # stealth PYTHON modules are required even static-only: scrapling.spiders.
    # session imports their session classes unconditionally (scrapling 0.4.2).
    "scrapling.fetchers.requests",
    "scrapling.fetchers.chrome",
    "scrapling.fetchers.stealth_chrome",
    # anyio selects its backend module by string at run time.
    "anyio._backends._asyncio",
]

datas = (
    collect_data_files("trafilatura")  # settings.cfg + language data
    # Fingerprint datapoints: scrapling's static header generation reads these
    # zips at run time (FileNotFoundError in the frozen build without them).
    + collect_data_files("apify_fingerprint_datapoints")
    + collect_data_files("browserforge")
)

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[os.path.join(SPECPATH, "..", "src")],
    hiddenimports=hiddenimports,
    datas=datas,
    # No module excludes: the playwright/patchright PYTHON packages cannot be
    # excluded (scrapling.fetchers.requests -> toolbelt.convertor -> playwright;
    # scrapling.spiders.session -> stealth_chrome -> patchright, both verified
    # against scrapling 0.4.2 source). "Static tier only" is enforced by
    # stripping the node DRIVERS below + browsers_available() short-circuiting
    # under sys.frozen, so no browser can ever launch from the frozen sidecar.
)

# Strip the browser drivers: dead weight in a static-only sidecar. `excludes`
# stops module imports but NOT hook-collected data files — patchright's 124MB
# node driver rides in as datas unless filtered here (measured, 2026-08-11).
_DRIVER_PREFIXES = ("playwright/driver", "patchright/driver")
a.datas = [e for e in a.datas if not e[0].startswith(_DRIVER_PREFIXES)]
a.binaries = [e for e in a.binaries if not e[0].startswith(_DRIVER_PREFIXES)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="iofast",
    console=True,  # the sidecar IS a stdio program; never build --windowed
)
coll = COLLECT(exe, a.binaries, a.datas, name="iofast")
