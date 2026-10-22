# -*- mode: python ; coding: utf-8 -*-
"""Frozen sidecar: uses patchright for all browser automation (playwright is
dev-only; its driver is stripped below). patchright's driver IS bundled so
social_browser sources (instagram, x) work in the frozen build.

Build:   .venv/bin/pyinstaller --noconfirm packaging/iofast.spec
Verify:  .venv/bin/python packaging/verify_frozen.py dist/iofast/iofast
The verify script IS the acceptance gate: it drives the frozen binary over
stdio JSON-RPC including a real scrape against a local fixture server.
"""

import os
import subprocess

from PyInstaller.utils.hooks import collect_all, collect_data_files

# Build provenance: stamp a git SHA into the bundle so verify_frozen.py (and
# the app<->sidecar handshake) can prove which source this freeze came from.
# The stamp is the LAST COMMIT THAT CHANGED THE BUNDLE INPUTS (src, packaging,
# pyproject) — not HEAD — so the app, which bakes the same value in at build
# time (app/src-tauri/build.rs), can compare by plain equality on any machine:
# equal = same engine sources; a docs-only commit between freeze and app build
# does not read as stale. Needs real history: CI checks out with fetch-depth 0.
# The file is gitignored; unfrozen runs ignore it and report "dev".
try:
    _rev = subprocess.run(
        ["git", "-C", SPECPATH, "log", "-1", "--format=%H", "--",
         "../src", "../packaging", "../pyproject.toml"],
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
        "iofast.spec: no git SHA available — build provenance requires a git checkout "
        f"with history (shallow clones: fetch-depth 0) ({_git_err})"
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
    # yt_dlp uses lazy plugin/extractor loading via string-keyed imports;
    # collect_all ensures every extractor (youtube, instagram, x/twitter) is
    # present in the frozen bundle so source detection works post-install.
    "yt_dlp",
    "yt_dlp.extractor",
    "yt_dlp.downloader",
    "yt_dlp.postprocessor",
    # patchright is the sole browser driver in frozen builds; its Python API
    # and bundled node driver must both be present. Playwright is dev-only.
    "patchright",
    "patchright.sync_api",
    "patchright.async_api",
    "httpx",
    # Source modules: explicit pins so --check-deps can verify them frozen.
    "iofast_engine.sources.youtube",
    "iofast_engine.sources.github",
    "iofast_engine.sources.instagram",
    "iofast_engine.sources.x",
    "iofast_engine.sources.web",
]

# yt_dlp uses lazy string-keyed imports for extractors, downloaders, and
# postprocessors — collect_all captures everything so YouTube/Instagram/X
# detection works in the frozen sidecar.
_yt_dlp_datas, _yt_dlp_binaries, _yt_dlp_hiddenimports = collect_all("yt_dlp")

# patchright's driver/ tree (node.exe + package/cli.js) is binary data that
# hiddenimports alone cannot capture. collect_all gathers the full driver so
# scrapling sessions and browser_helper can spawn the node subprocess in
# frozen builds. Without this, every browser launch fails with FileNotFoundError.
_patchright_datas, _patchright_binaries, _patchright_hiddenimports = collect_all("patchright")

datas = (
    collect_data_files("trafilatura")  # settings.cfg + language data
    # Fingerprint datapoints: scrapling's static header generation reads these
    # zips at run time (FileNotFoundError in the frozen build without them).
    + collect_data_files("apify_fingerprint_datapoints")
    + collect_data_files("browserforge")
    + _yt_dlp_datas
    + _patchright_datas
)

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[os.path.join(SPECPATH, "..", "src")],
    hiddenimports=hiddenimports + _yt_dlp_hiddenimports,
    datas=datas,
    # No module excludes: the playwright/patchright PYTHON packages cannot be
    # excluded (scrapling.fetchers.requests -> toolbelt.convertor -> playwright;
    # scrapling.spiders.session -> stealth_chrome -> patchright, both verified
    # against scrapling 0.4.2 source). Playwright's node driver MUST be present
    # for social_browser sources (instagram, x) that use channel="msedge"/
    # channel="chrome" — only patchright's driver is stripped (dead weight).
)

# Strip ONLY playwright's driver: patchright is the sole browser driver in
# frozen builds and its node driver must remain bundled. Playwright's driver
# is dead weight (dev-only dependency).
_PLAYWRIGHT_PREFIXES = ("playwright/driver",)
a.datas = [e for e in a.datas if not e[0].startswith(_PLAYWRIGHT_PREFIXES)]
a.binaries = [e for e in a.binaries if not e[0].startswith(_PLAYWRIGHT_PREFIXES)]

# Merge yt_dlp and patchright binaries from collect_all into the analysis.
# patchright's node.exe driver is a binary that must be present for any
# browser launch (social_browser, dynamic tier, spider sessions).
a.binaries += _yt_dlp_binaries + _patchright_binaries

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="iofast",
    console=True,  # the sidecar IS a stdio program; never build --windowed
)
coll = COLLECT(exe, a.binaries, a.datas, name="iofast")
