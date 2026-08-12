"""Generate THIRD-PARTY-NOTICES.md from the venv's INSTALLED package metadata.

Ground truth is `importlib.metadata`, never memory: a wrong license is a legal
defect, so every license id here is read from the distribution that is actually
installed (and shipped in the frozen sidecar), or honestly marked UNKNOWN.

Method
------
1. Direct runtime deps are parsed from ``engine/pyproject.toml``
   ``[project].dependencies`` (names + extras, version specifiers stripped —
   ``scrapling[fetchers]==0.4.2`` -> name ``scrapling``, extras ``{fetchers}``).
2. The shipped bundle is the TRANSITIVE closure of those direct deps: a BFS over
   ``importlib.metadata.requires()``. A requirement gated by ``extra == "X"`` is
   followed only when X was requested (so ``scrapling[fetchers]`` pulls in
   curl_cffi / browserforge / playwright, but no dev/test/ai extras). Anything
   not actually installed is dropped (platform-specific wheels never shipped).
3. Each package's license: ``License-Expression`` (SPDX) -> single-line
   ``License`` id -> ``Classifier: License :: ...`` (mapped to an SPDX-ish id) ->
   ``UNKNOWN``. Full multi-line license TEXT in the ``License`` field is not
   treated as an id; it falls through to the classifier.
4. Chromium is appended as a curated NON-pip entry: the dynamic tier downloads it
   at runtime, it is never bundled in the sidecar.

# ponytail: closure honors `extra ==` markers only; other env markers
# (python_version, sys_platform) are over-included then filtered by "is it
# installed". Over-crediting a license is safe; under-crediting is the defect.
# ponytail: license TEXT is not embedded, only the id + the packaged LICENSE
# file's name if present. Full-text inclusion is the upgrade path if a distributor
# requires verbatim texts.

Stdlib only. CLI: writes repo-root THIRD-PARTY-NOTICES.md; `--check` fails if
stale (CI); `--out PATH` overrides the target.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

# script is engine/packaging/gen_notices.py -> parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# Classifier trailer -> short license id. Anything unmapped is returned verbatim
# (honest) rather than guessed.
_CLASSIFIER_MAP = {
    "BSD License": "BSD",
    "Apache Software License": "Apache-2.0",
    "MIT License": "MIT",
    "MIT No Attribution License (MIT-0)": "MIT-0",
    "ISC License (ISCL)": "ISC",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "PSF-2.0",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.0+",
}

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_EXTRAS_RE = re.compile(r"\[([^\]]*)\]")
_EXTRA_MARKER_RE = re.compile(r"extra\s*==\s*['\"]([^'\"]+)['\"]")


def parse_requirement(spec: str) -> tuple[str, frozenset[str]]:
    """`scrapling[fetchers]==0.4.2` -> ('scrapling', {'fetchers'}). '' name if unparseable."""
    m = _NAME_RE.match(spec)
    name = m.group(1) if m else ""
    em = _EXTRAS_RE.search(spec)
    extras = (
        frozenset(e.strip() for e in em.group(1).split(",") if e.strip()) if em else frozenset()
    )
    return name, extras


def direct_deps(pyproject_path: Path = PYPROJECT) -> list[tuple[str, frozenset[str]]]:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return [parse_requirement(d) for d in data["project"]["dependencies"]]


def direct_dep_names(pyproject_path: Path = PYPROJECT) -> set[str]:
    return {name for name, _ in direct_deps(pyproject_path) if name}


def classify_license(classifier: str) -> str | None:
    """Map a `License :: ...` classifier to a short id. None if not a License classifier."""
    if not classifier.startswith("License"):
        return None
    trailer = classifier.split("::")[-1].strip()
    return _CLASSIFIER_MAP.get(trailer, trailer)


def resolve_license(
    license_expression: str | None,
    license_field: str | None,
    classifiers: list[str],
) -> str:
    """Field precedence: SPDX expression -> single-line License id -> classifier -> UNKNOWN."""
    if license_expression and license_expression.strip():
        return license_expression.strip()
    lic = (license_field or "").strip()
    # A short single-line value is an id; multi-line text is a full license body.
    if lic and "\n" not in lic and len(lic) <= 40:
        return lic
    for c in classifiers:
        mapped = classify_license(c)
        if mapped:
            return mapped
    if lic:  # had full text but no classifier: first line beats UNKNOWN
        return lic.splitlines()[0].strip()
    return "UNKNOWN"


def license_of(dist: metadata.Distribution) -> str:
    md = dist.metadata
    return resolve_license(
        md.get("License-Expression"),
        md.get("License"),
        md.get_all("Classifier") or [],
    )


def license_file_note(dist: metadata.Distribution) -> str | None:
    """Name of a packaged LICENSE/COPYING file, if the wheel shipped one."""
    for f in dist.files or []:
        base = f.name.upper()
        if base.startswith(("LICENSE", "COPYING", "NOTICE")):
            return f.name
    return None


def closure(direct: list[tuple[str, frozenset[str]]]) -> dict[str, metadata.Distribution]:
    """BFS the installed dependency closure. Keyed by canonical dist name."""
    found: dict[str, metadata.Distribution] = {}
    seen: set[tuple[str, frozenset[str]]] = set()
    queue = list(direct)
    while queue:
        name, extras = queue.pop()
        key = (name.lower().replace("_", "-"), extras)
        if not name or key in seen:
            continue
        seen.add(key)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # platform-specific / not shipped
        found[dist.metadata["Name"]] = dist
        for req in dist.requires or []:
            marker = _EXTRA_MARKER_RE.search(req)
            if marker and marker.group(1) not in extras:
                continue  # extra not requested
            queue.append(parse_requirement(req))
    return found


_CHROMIUM = (
    "Chromium",
    "runtime download",
    "BSD-3-Clause",
    "(c) The Chromium Authors — downloaded at runtime by the dynamic tier, NOT bundled.",
)


def render() -> str:
    dists = closure(direct_deps())
    lines = [
        "# Third-Party Notices",
        "",
        "This file lists the open-source packages bundled in the iofast frozen sidecar",
        "and their licenses. It is generated by `engine/packaging/gen_notices.py` from the",
        "**installed package metadata** (`importlib.metadata`) — never from memory — as the",
        "transitive closure of the direct runtime dependencies in `engine/pyproject.toml`.",
        "Regenerate with `python engine/packaging/gen_notices.py`; CI can gate with `--check`.",
        "",
        "License ids come from each distribution's `License-Expression`, `License`, or",
        "`License ::` classifier. `UNKNOWN` means the installed metadata declared no license",
        "(never a guess). Full license texts are not embedded — see each package's project page.",
        "",
        "**Chromium** is listed but is NOT a pip package and is NOT bundled: the dynamic",
        "browser tier downloads it at runtime.",
        "",
    ]
    rows: list[tuple[str, str, str, str | None]] = []
    for dist in dists.values():
        name = dist.metadata["Name"]
        rows.append((name, dist.version, license_of(dist), license_file_note(dist)))
    rows.sort(key=lambda r: r[0].lower())

    for name, version, lic, note in rows:
        lines.append(f"### {name} {version}")
        lines.append(f"License: {lic}")
        if note:
            lines.append(f"License file (in package): {note}")
        lines.append("")

    name, version, lic, note = _CHROMIUM
    lines.append(f"### {name} ({version})")
    lines.append(f"License: {lic}")
    lines.append(note)
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate THIRD-PARTY-NOTICES.md.")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "THIRD-PARTY-NOTICES.md")
    ap.add_argument("--check", action="store_true", help="fail if the on-disk file is stale")
    args = ap.parse_args(argv)

    content = render()
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != content:
            print(f"STALE: {args.out} differs from generated output. Run gen_notices.py.", file=sys.stderr)
            return 1
        print(f"OK: {args.out} is up to date.")
        return 0
    args.out.write_text(content, encoding="utf-8")
    print(f"Wrote {args.out} ({len(content.splitlines())} lines).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
