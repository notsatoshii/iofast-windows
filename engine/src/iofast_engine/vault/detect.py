"""Detect Obsidian vaults from the Obsidian config file.

Reads %APPDATA%/obsidian/obsidian.json (Windows) or the platform equivalent.
Returns a list of vault dicts with path, id, and open status.
"""

import json
import os
import platform
from pathlib import Path


def _obsidian_config_path() -> Path | None:
    """Platform-specific path to obsidian.json."""
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "obsidian" / "obsidian.json"
        return None
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    else:  # Linux
        config = os.environ.get("XDG_CONFIG_HOME")
        if config:
            return Path(config) / "obsidian" / "obsidian.json"
        return Path.home() / ".config" / "obsidian" / "obsidian.json"


def detect_vaults() -> list[dict]:
    """Return list of vaults from Obsidian config. Empty list if not found."""
    config_path = _obsidian_config_path()
    if config_path is None or not config_path.is_file():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    vaults_raw = data.get("vaults", {})
    if not isinstance(vaults_raw, dict):
        return []
    results = []
    for vault_id, info in vaults_raw.items():
        if not isinstance(info, dict):
            continue
        path = info.get("path")
        if not isinstance(path, str):
            continue
        results.append({
            "id": vault_id,
            "path": path,
            "open": bool(info.get("open", False)),
        })
    return results