"""Regenerate src/iofast_engine/browser_pins.py from PyPI.

Pins the mac wheels of the INSTALLED playwright/patchright versions (so the
pins can never drift from the environment the frozen bundle was built from),
recording PyPI's official sha256 digest per wheel. Run after bumping either
package, then commit the regenerated file:

    .venv/bin/python packaging/gen_browser_pins.py
"""

import importlib.metadata
import json
import os
import urllib.request

PACKAGES = ("playwright", "patchright")
# machine() value -> wheel filename marker
MAC_ARCHES = {"arm64": "macosx_11_0_arm64", "x86_64": "macosx_10_13_x86_64"}

HEADER = '''"""Pinned out-of-band browser-driver wheels — GENERATED, do not edit.

Regenerate with `packaging/gen_browser_pins.py` after bumping playwright or
patchright. sha256 digests are PyPI's own, recorded at generation time; the
downloader refuses any payload that does not match (see browsers.py).
"""

'''


def pypi_wheels(package: str, version: str) -> dict:
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/{package}/{version}/json", timeout=30
    ) as response:
        urls = json.load(response)["urls"]
    wheels = {}
    for arch, marker in MAC_ARCHES.items():
        entry = next(u for u in urls if marker in u["filename"])
        wheels[arch] = {
            "url": entry["url"],
            "sha256": entry["digests"]["sha256"],
            "size": entry["size"],
        }
    return wheels


def main():
    pins = {}
    for package in PACKAGES:
        version = importlib.metadata.version(package)
        pins[package] = {"version": version, "wheels": pypi_wheels(package, version)}
    out = os.path.join(
        os.path.dirname(__file__), "..", "src", "iofast_engine", "browser_pins.py"
    )
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(HEADER)
        handle.write("PINS = ")
        handle.write(json.dumps(pins, indent=4).replace("true", "True").replace("false", "False"))
        handle.write("\n")
    print(f"wrote {os.path.normpath(out)}")
    for package, pin in pins.items():
        print(f"  {package} {pin['version']}: {', '.join(pin['wheels'])}")


if __name__ == "__main__":
    main()
