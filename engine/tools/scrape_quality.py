"""Scrape-quality detection pass — finds the #29 failure class without eyeballing.

Runs a bounded engine scrape of a docs root (or reuses an existing --out), then
flags low-yield extraction from the manifest:

  * thin pages: outcome ok but chars under the floor
  * HTML-extract reasons (selector+trafilatura / selector+markdownify / trafilatura)
    on pages that came out thin — the signature of extracting nav/fragment
  * site link_ratio over the clean cap (extracted nav, not prose)
  * uniform-tiny: most pages in one small char band = systemic extractor miss
  * whether the .md-twin path (llms.txt) was used at all

Also probes cheap shape hints out-of-band: meta generator tag, /llms.txt,
raw `.md` / `.mdx` twin on a sample page.

Usage (from engine/):
  PYTHONPATH=src .venv/bin/python tools/scrape_quality.py URL \
      [--scope P] [--max-pages N] [--out DIR] [--skip-scrape]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from urllib.parse import urlsplit, urlunsplit

# analyse() ships inside the engine now (every run records its own verdict), so
# the harness reads it from there rather than keeping a second copy that drifts.
from iofast_engine.quality import analyse

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) iofast-quality-probe"


def _get(url: str, limit: int = 65536) -> tuple[int, str, str]:
    """(status, content_type, body[:limit]) — never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, ctype, resp.read(limit).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), ""
    except Exception as exc:  # noqa: BLE001 — probe, not control flow
        return 0, "", f"{type(exc).__name__}: {exc}"


def shape_hints(root_url: str, sample_page: str | None) -> dict:
    parts = urlsplit(root_url)
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    hints: dict = {}

    status, _, body = _get(root_url)
    m = re.search(
        r'<meta[^>]+name=["\']?generator["\']?[^>]+content=["\']?([^"\'>]+)', body, re.I
    )
    hints["generator"] = m.group(1) if m else None
    hints["root_status"] = status

    status, ctype, body = _get(origin + "/llms.txt", limit=4096)
    hints["llms_txt"] = {"status": status, "content_type": ctype.split(";")[0]}

    if sample_page:
        page = sample_page.rstrip("/")
        for ext in (".md", ".mdx"):
            status, ctype, body = _get(page + ext, limit=4096)
            looks_md = status == 200 and "html" not in ctype and len(body.strip()) > 100
            hints[f"twin{ext}"] = {
                "url": page + ext,
                "status": status,
                "content_type": ctype.split(";")[0],
                "plausible": looks_md,
            }
    return hints


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url")
    ap.add_argument("--scope", default=None)
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--out", default=None)
    ap.add_argument("--skip-scrape", action="store_true", help="reuse existing --out manifest")
    args = ap.parse_args()

    out = args.out or os.path.join(
        "/tmp", "scrape-quality", re.sub(r"[^A-Za-z0-9.-]+", "-", urlsplit(args.url).netloc)
    )

    if not args.skip_scrape:
        cmd = [
            sys.executable, "-m", "iofast_engine.cli", "scrape", args.url,
            "--max-pages", str(args.max_pages), "--out", out,
        ]
        if args.scope:
            cmd += ["--scope", args.scope]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            stderr = proc.stderr[-2000:]
        except subprocess.TimeoutExpired:
            stderr = "scrape timed out after 600s"
        if not os.path.exists(os.path.join(out, "manifest.json")):
            print(json.dumps({"verdict": "SCRAPE_FAILED", "stderr": stderr}))
            return 1

    manifest_path = os.path.join(out, "manifest.json")
    if not os.path.exists(manifest_path):
        print(json.dumps({"verdict": "SCRAPE_FAILED", "stderr": f"no manifest at {manifest_path}"}))
        return 1
    manifest = json.load(open(manifest_path))
    report = analyse(manifest)

    sample = None
    ok_pages = [p for p in manifest.get("pages", []) if p.get("outcome") == "ok"]
    if ok_pages:
        sample = ok_pages[0]["url"]
    report["hints"] = shape_hints(args.url, sample)
    report["out"] = out

    with open(os.path.join(out, "quality.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
