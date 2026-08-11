"""The numeric "clean" definition — every number below is measured, not remembered.

Measured against the committed fixture matrix + the Phase 0 baseline runs
(2026-08-11):
- junk/shell extractions top out at 89 chars (notion shell 89, readme shell 44);
  the smallest real page is 132 (mintlify's nav-only landing). MIN_CHARS = 100
  sits in that gap: zero real pages flagged, every junk file caught.
- content pages measure a link-to-text ratio of 0.04-0.37; genuine index/TOC
  pages reach 0.88 but are a small minority of any real corpus. The cap is
  therefore CORPUS-WIDE at 0.50 — a whole corpus averaging past 0.5 is nav
  shells, while one legit link-hub page cannot fail a healthy run.
- coverage >= 0.95 of in-scope URLs producing a file is PLAN's fixed bar.
"""

import os
import re

COVERAGE_FLOOR = 0.95
MIN_CHARS = 100
LINK_RATIO_CAP = 0.50

_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_ENVELOPE_LINES = 2  # SOURCE line + UNTRUSTED banner


def _body(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return "".join(fh.readlines()[_ENVELOPE_LINES:]).strip()


def evaluate(ledger, out_dir: str) -> dict:
    """Clean verdict for a finished run: coverage, char floor, corpus link ratio."""
    enqueued = len(ledger.enqueued)
    written = [r for r in ledger.records.values() if r.get("file")]
    coverage = (len(written) / enqueued) if enqueued else 0.0

    under_min, total_chars, link_chars = [], 0, 0
    for record in written:
        path = os.path.join(out_dir, record["file"])
        try:
            text = _body(path)
        except OSError:
            under_min.append(record["file"])
            continue
        if len(text) < MIN_CHARS:
            under_min.append(record["file"])
        total_chars += len(text)
        link_chars += sum(len(m.group(0)) for m in _LINK_RE.finditer(text))

    link_ratio = (link_chars / total_chars) if total_chars else 0.0
    checks = {
        "coverage": {"value": round(coverage, 4), "floor": COVERAGE_FLOOR, "ok": coverage >= COVERAGE_FLOOR},
        "min_chars": {"floor": MIN_CHARS, "files_under": under_min, "ok": not under_min},
        "link_ratio": {"value": round(link_ratio, 4), "cap": LINK_RATIO_CAP, "ok": link_ratio <= LINK_RATIO_CAP},
    }
    return {"clean": all(c["ok"] for c in checks.values()), **checks}
