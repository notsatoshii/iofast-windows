"""The `scrape.env` allowlist is duplicated in three files with no shared source
(docs/RPC.md contract): the engine's `ALLOWED_ENV`, the app's Rust `KEY_NAMES`,
and the app's TypeScript key tables. A key added to one but not the others is a
silent drift — the app would offer a key the engine drops as -32602, or hold a
secret the UI can never set. This test is the cross-check: all three must list
the identical set of keys.

It reads the two app source files directly. engine-ci triggers on `engine/**`,
so an engine-side edit re-runs it; an app-only edit lands with NO CI (see the
handoff), so the frozen-gate/app-merge ritual is the backstop there.
"""

import re
from pathlib import Path

from iofast_engine.rpc import ALLOWED_ENV

REPO = Path(__file__).resolve().parents[2]
STORE_RS = REPO / "app" / "src-tauri" / "src" / "store.rs"
SETTINGS_TSX = REPO / "app" / "src" / "main" / "Settings.tsx"


def _rust_key_names(text):
    body = re.search(r"KEY_NAMES:\s*\[&str;\s*\d+\]\s*=\s*\[(.*?)\]", text, re.S)
    assert body, "KEY_NAMES array not found in store.rs — did it move or rename?"
    return set(re.findall(r'"([^"]+)"', body.group(1)))


def _tsx_key_names(text):
    keys = set()
    for name in ("RESCUE_KEYS", "NETWORK_KEYS"):
        body = re.search(rf"{name}\s*=\s*\[(.*?)\]\s*as const", text, re.S)
        assert body, f"{name} array not found in Settings.tsx — did it move or rename?"
        keys |= set(re.findall(r'name:\s*"([^"]+)"', body.group(1)))
    return keys


def test_env_allowlist_agrees_across_engine_rust_and_tsx():
    rust = _rust_key_names(STORE_RS.read_text())
    tsx = _tsx_key_names(SETTINGS_TSX.read_text())
    assert set(ALLOWED_ENV) == rust == tsx, (
        "scrape.env allowlist drifted across the three sources:\n"
        f"  rpc.py ALLOWED_ENV : {sorted(ALLOWED_ENV)}\n"
        f"  store.rs KEY_NAMES : {sorted(rust)}\n"
        f"  Settings.tsx keys  : {sorted(tsx)}"
    )
