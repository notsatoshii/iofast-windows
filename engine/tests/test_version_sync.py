"""Every version number in the repo must equal the root VERSION file.

Cheap guard for docs/RELEASING.md: runs in the Linux CI job on every PR.
"""

import json
import re
import tomllib
from pathlib import Path

from iofast_engine import __version__

ROOT = Path(__file__).resolve().parents[2]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), VERSION


def test_engine_reports_version():
    # rpc.py's initialize reply sends this as "engine".
    assert __version__ == VERSION


def test_pyproject():
    data = tomllib.loads((ROOT / "engine" / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == VERSION


def test_cargo_toml():
    data = tomllib.loads((ROOT / "app" / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))
    assert data["package"]["version"] == VERSION


def test_tauri_conf_uses_cargo_version():
    conf = json.loads((ROOT / "app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert "version" not in conf, "tauri.conf.json must not carry its own version"


def test_package_json():
    pkg = json.loads((ROOT / "app" / "package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == VERSION


def test_gradle_reads_version_file():
    gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert 'versionName = rootProject.file("../VERSION").readText().trim()' in gradle
