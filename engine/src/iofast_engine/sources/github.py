"""GitHub source: download a repo archive and render it as LLM-ready markdown.

No git binary needed — we fetch the tarball via codeload.github.com and the
repo metadata via the REST API. Public repos work without auth; private repos
need GITHUB_TOKEN in the environment (passed in by the shell, never logged).
"""

import fnmatch
import io
import json
import os
import re
import tarfile
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

from ..ledger import Ledger
from ..writer import envelope, write_run
from .base import GrabOptions, GrabResult

# Folders that are never interesting source code. Skipped before size checks
# so we don't waste the 50 MB budget on dependency trees.
SKIP_DIRS = {
    "node_modules", "vendor", "dist", "build", "__pycache__", ".git",
    ".next", ".nuxt", ".cache", ".tox", ".eggs", "*.egg-info",
}

# Lockfiles carry no prose and bloat the output.
SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.lock", "go.sum", "poetry.lock", "Pipfile.lock",
    "composer.lock", "Gemfile.lock",
}

# Per-file ceiling. Files above this are listed in report.md but not saved.
FILE_SIZE_CAP = 1 * 1024 * 1024  # 1 MB

# Total budget across all source files. Once hit, remaining files are skipped
# and listed in the report so the user knows what was dropped.
TOTAL_SIZE_CAP = 50 * 1024 * 1024  # 50 MB

# GitHub URL pattern: owner/repo with optional /tree/<ref>/<path> or /blob/<ref>/<path>.
GITHUB_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)"
    r"(?:/(?:tree|blob)/[^/]+(?:/.*)?)?/?$"
)


def _parse_github_url(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL, or None if it doesn't match."""
    m = GITHUB_RE.match(url.strip())
    return (m.group(1), m.group(2)) if m else None


def _is_binary(data: bytes) -> bool:
    """Heuristic: file is binary if the first 8 KB contain null bytes."""
    return b"\x00" in data[:8192]


def _load_gitignore_patterns(names: list[str], contents: dict[str, bytes]) -> list[str]:
    """Collect .gitignore patterns from every directory in the archive.

    Returns a flat list of (dir_prefix, pattern) tuples so we can match
    relative to each directory, matching git's own scoping rules.
    """
    patterns: list[tuple[str, str]] = []
    for name in names:
        if name.endswith("/.gitignore"):
            dir_prefix = name.rsplit("/", 1)[0] + "/"
            text = contents.get(name, b"").decode("utf-8", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append((dir_prefix, line))
    return patterns


def _matches_gitignore(
    path: str, patterns: list[tuple[str, str]]
) -> bool:
    """True if path matches any .gitignore pattern scoped to its directory."""
    for dir_prefix, pattern in patterns:
        # Pattern applies to this path only if the path lives under the
        # directory that owns the .gitignore (or at the root).
        if dir_prefix and not path.startswith(dir_prefix):
            continue
        rel = path[len(dir_prefix):] if dir_prefix else path
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(os.path.basename(rel), pattern):
            return True
    return False


def _skip_dir(name: str) -> bool:
    """True if this directory component should be pruned entirely."""
    base = name.rstrip("/").rsplit("/", 1)[-1]
    return base in SKIP_DIRS or any(fnmatch.fnmatch(base, p) for p in SKIP_DIRS)


class GitHubSource:
    """Downloads a GitHub repo as an archive and renders source as markdown."""

    name = "github"

    def matches(self, url: str) -> bool:
        return _parse_github_url(url) is not None

    async def grab(
        self, url: str, out_root: Path, opts: GrabOptions, emit
    ) -> GrabResult:
        parsed = _parse_github_url(url)
        if not parsed:
            raise ValueError(f"not a GitHub URL: {url}")
        owner, repo = parsed

        run_dir = out_root / f"github-{owner}-{repo}"
        run_dir.mkdir(parents=True, exist_ok=True)
        ledger = Ledger()
        emit({"type": "info", "message": f"📦 grabbing {owner}/{repo} from GitHub"})

        # Fetch repo metadata and archive via stdlib (no aiohttp dep).
        token = os.environ.get("GITHUB_TOKEN", "")
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        archive_url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/HEAD"

        try:
            req_meta = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req_meta, timeout=30) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            reason = f"API returned {e.code}"
            ledger.record(url, "failed_fetch", reason)
            emit({"type": "error", "message": f"❌ {reason}"})
            write_run(str(run_dir), ledger, {"source": "github", "url": url})
            return GrabResult(source=self.name, run_dir=run_dir, ok=0, thin=0, failed=1)
        except Exception as e:
            reason = f"API request failed: {e}"
            ledger.record(url, "failed_fetch", reason)
            emit({"type": "error", "message": f"❌ {reason}"})
            write_run(str(run_dir), ledger, {"source": "github", "url": url})
            return GrabResult(source=self.name, run_dir=run_dir, ok=0, thin=0, failed=1)

        try:
            req_archive = urllib.request.Request(archive_url, headers=headers)
            with urllib.request.urlopen(req_archive, timeout=120) as resp:
                archive_data = resp.read()
        except urllib.error.HTTPError as e:
            reason = f"archive download returned {e.code}"
            ledger.record(url, "failed_fetch", reason)
            emit({"type": "error", "message": f"❌ {reason}"})
            write_run(str(run_dir), ledger, {"source": "github", "url": url})
            return GrabResult(source=self.name, run_dir=run_dir, ok=0, thin=0, failed=1)
        except Exception as e:
            reason = f"archive download failed: {e}"
            ledger.record(url, "failed_fetch", reason)
            emit({"type": "error", "message": f"❌ {reason}"})
            write_run(str(run_dir), ledger, {"source": "github", "url": url})
            return GrabResult(source=self.name, run_dir=run_dir, ok=0, thin=0, failed=1)

        # Extract tarball into memory so we never touch disk with untrusted
        # archive paths (zip-slip protection).
        contents: dict[str, bytes] = {}
        try:
            buf = io.BytesIO(archive_data)
            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    # Strip the top-level directory that GitHub adds (repo-ref/).
                    parts = member.name.split("/", 1)
                    if len(parts) < 2:
                        continue
                    rel_path = parts[1]
                    if not rel_path:
                        continue
                    fobj = tar.extractfile(member)
                    if fobj is None:
                        continue
                    contents[rel_path] = fobj.read()
        except (tarfile.TarError, OSError) as exc:
            reason = f"archive extraction failed: {exc}"
            ledger.record(url, "failed_exception", reason)
            emit({"type": "error", "message": f"❌ {reason}"})
            write_run(str(run_dir), ledger, {"source": "github", "url": url})
            return GrabResult(source=self.name, run_dir=run_dir, ok=0, thin=0, failed=1)

        # Build .gitignore index before filtering.
        gi_patterns = _load_gitignore_patterns(list(contents.keys()), contents)

        # Classify files: keep, skip-reason pairs.
        kept: list[tuple[str, bytes]] = []
        skipped: list[tuple[str, str]] = []
        total_bytes = 0

        for rel_path, data in sorted(contents.items()):
            # Prune entire directories early.
            if any(_skip_dir(p) for p in rel_path.split("/")[:-1]):
                skipped.append((rel_path, "vendored/build directory"))
                continue
            if _skip_dir(rel_path):
                skipped.append((rel_path, "vendored/build directory"))
                continue

            # Skip lockfiles by name.
            basename = rel_path.rsplit("/", 1)[-1]
            if basename in SKIP_FILES:
                skipped.append((rel_path, "lockfile"))
                continue

            # Respect .gitignore.
            if _matches_gitignore(rel_path, gi_patterns):
                skipped.append((rel_path, ".gitignore"))
                continue

            # Binary check.
            if _is_binary(data):
                skipped.append((rel_path, "binary"))
                continue

            # Per-file size cap.
            if len(data) > FILE_SIZE_CAP:
                skipped.append((rel_path, f"over {FILE_SIZE_CAP // 1024 // 1024} MB"))
                continue

            # Total budget.
            if total_bytes + len(data) > TOTAL_SIZE_CAP:
                skipped.append((rel_path, "total size cap reached"))
                continue

            total_bytes += len(data)
            kept.append((rel_path, data))

        # Write 00-overview.md with repo metadata and file tree.
        topics = ", ".join(meta.get("topics", [])) or "none"
        license_info = (meta.get("license") or {}).get("spdx_id", "unspecified")
        tree_lines = [f"- {p}" for p, _ in kept]
        overview = (
            f"# {owner}/{repo}\n\n"
            f"{meta.get('description') or 'No description.'}\n\n"
            f"- **Stars**: {meta.get('stargazers_count', 0)}\n"
            f"- **Language**: {meta.get('language') or 'unknown'}\n"
            f"- **License**: {license_info}\n"
            f"- **Default branch**: {meta.get('default_branch', 'main')}\n"
            f"- **Topics**: {topics}\n\n"
            f"## Files ({len(kept)})\n\n"
            + "\n".join(tree_lines)
            + "\n"
        )
        overview_url = f"https://github.com/{owner}/{repo}"
        with open(run_dir / "00-overview.md", "w", encoding="utf-8") as fh:
            fh.write(envelope(overview_url, overview))
        ledger.record(overview_url, "ok", "overview", file="00-overview.md", chars=len(overview))

        # Write README.md from the archive if present.
        readme_key = next((k for k in contents if k.lower() == "readme.md"), None)
        if readme_key and (readme_key, contents[readme_key]) in kept:
            readme_text = contents[readme_key].decode("utf-8", errors="replace")
            with open(run_dir / "README.md", "w", encoding="utf-8") as fh:
                fh.write(envelope(overview_url, readme_text))
            ledger.record(overview_url, "ok", "readme", file="README.md", chars=len(readme_text))

        # Group source files by directory and write one markdown per directory.
        dirs: dict[str, list[tuple[str, bytes]]] = {}
        for rel_path, data in kept:
            if rel_path.lower() == "readme.md":
                continue  # already written above
            parent = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
            dirs.setdefault(parent, []).append((rel_path, data))

        ok_count = 2 if readme_key else 1  # overview + maybe readme
        for dir_name, files in sorted(dirs.items()):
            safe_name = dir_name.replace("/", "-").replace(".", "_") or "root"
            md_name = f"src-{safe_name}.md"
            lines = [f"# {dir_name or '(root)'}\n"]
            for rel_path, data in files:
                text = data.decode("utf-8", errors="replace")
                ext = rel_path.rsplit(".", 1)[-1] if "." in rel_path else ""
                lang = ext if ext in {"py", "js", "ts", "go", "rs", "java", "c", "cpp", "rb", "sh", "yaml", "yml", "json", "toml", "md"} else ""
                lines.append(f"## {rel_path}\n\n```{lang}\n{text}\n```\n")
            body = "\n".join(lines)
            with open(run_dir / md_name, "w", encoding="utf-8") as fh:
                fh.write(envelope(overview_url, body))
            ledger.record(overview_url, "ok", "source group", file=md_name, chars=len(body))
            ok_count += 1

        # Record skipped files so the report shows what was dropped.
        for rel_path, reason in skipped:
            ledger.exclude(reason)

        # Finalize: manifest.json, report.md, README-UNTRUSTED.md.
        # Use repo description or owner/repo as title for vault filing.
        repo_title = meta.get("description") or f"{owner}/{repo}"
        info = {
            "source": "github",
            "url": url,
            "title": repo_title,
            "owner": owner,
            "repo": repo,
            "files_kept": len(kept),
            "files_skipped": len(skipped),
            "total_bytes": total_bytes,
        }
        write_run(str(run_dir), ledger, info)

        counts = ledger.counts()
        failed = sum(counts[c] for c in ("failed_fetch", "failed_exception", "skipped_no_content"))
        emit({
            "type": "done",
            "message": f"✅ {owner}/{repo}: {ok_count} files, {len(skipped)} skipped",
        })
        return GrabResult(
            source=self.name, run_dir=run_dir, ok=ok_count, thin=0, failed=failed
        )