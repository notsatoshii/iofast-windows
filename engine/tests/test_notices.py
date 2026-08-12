"""THIRD-PARTY-NOTICES generator: license classifier mapping, pyproject parse, freshness.

Offline — no venv metadata needed for the pure-function checks; the file-content
check reads the committed THIRD-PARTY-NOTICES.md (guards against a stale/missing file).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))

import gen_notices as gn


def test_classifier_mapping_known():
    assert gn.classify_license("License :: OSI Approved :: BSD License") == "BSD"
    assert gn.classify_license("License :: OSI Approved :: Apache Software License") == "Apache-2.0"
    assert gn.classify_license("License :: OSI Approved :: MIT License") == "MIT"


def test_unknown_license_is_never_guessed():
    # No expression, no License field, no classifiers -> honest UNKNOWN, not a fabrication.
    assert gn.resolve_license(None, None, []) == "UNKNOWN"
    assert gn.resolve_license("", "", []) == "UNKNOWN"
    # A non-license classifier alone still yields UNKNOWN.
    assert gn.resolve_license(None, "", ["Programming Language :: Python :: 3"]) == "UNKNOWN"


def test_resolve_license_precedence():
    assert gn.resolve_license("Apache-2.0", "ignored", []) == "Apache-2.0"
    assert gn.resolve_license(None, "BSD-3-Clause", []) == "BSD-3-Clause"
    assert gn.resolve_license(None, None, ["License :: OSI Approved :: MIT License"]) == "MIT"
    # Multi-line full text in License field is not an id -> fall through to classifier.
    full_text = "BSD 3-Clause License\n\nCopyright (c) 2024\nRedistribution..."
    assert gn.resolve_license(None, full_text, ["License :: OSI Approved :: BSD License"]) == "BSD"


def test_pyproject_direct_deps_parse():
    names = gn.direct_dep_names()
    assert names, "no direct deps parsed from pyproject.toml"
    # extras + version specifier stripping: scrapling[fetchers]==0.4.2 -> scrapling
    assert {"scrapling", "playwright", "lxml"} <= names


def test_parse_requirement_strips_extras_and_specifiers():
    assert gn.parse_requirement("scrapling[fetchers]==0.4.2") == ("scrapling", frozenset({"fetchers"}))
    assert gn.parse_requirement("lxml==6.1.1") == ("lxml", frozenset())


def test_notices_file_exists_and_current():
    path = gn.REPO_ROOT / "THIRD-PARTY-NOTICES.md"
    assert path.exists(), f"{path} missing — run gen_notices.py"
    text = path.read_text(encoding="utf-8")
    for token in ("Chromium",):  # the curated non-pip runtime download
        assert token in text, f"{token} missing from THIRD-PARTY-NOTICES.md"
    # Every PINNED direct dependency must appear — a new lib added to pyproject
    # without regenerating notices fails here (non-flaky: directs are explicit,
    # unlike floating transitive versions a strict --check would thrash on).
    for name in gn.direct_dep_names():
        assert name in text, f"direct dep {name} missing from THIRD-PARTY-NOTICES.md — regenerate it"
