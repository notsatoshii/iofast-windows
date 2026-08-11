"""Import purity: the pure modules must never drag in the fetch/browser stack.

A module that imports scrapling or playwright at module level cannot be used
offline (fixtures, CI, adapters), and a lazy import buried in a function is the
same trap one call deeper — so this asserts on sys.modules after the import.
"""

import subprocess
import sys

import pytest

PURE = ["urls", "ledger", "extract", "writer", "manifest", "adapters.base", "adapters"]
FORBIDDEN = ("scrapling", "playwright", "patchright")

PROBE = """
import sys
import iofast_engine.{module}
loaded = [m for m in sys.modules if m.split(".")[0] in {forbidden!r}]
assert not loaded, "{module} pulled in: " + ", ".join(sorted(loaded))
"""


@pytest.mark.parametrize("module", PURE)
def test_module_imports_without_fetch_stack(module):
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(module=module, forbidden=FORBIDDEN)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_discovery_module_level_is_pure():
    """discovery.py may reach scrapling only lazily, inside _get()."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(module="discovery", forbidden=FORBIDDEN)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
