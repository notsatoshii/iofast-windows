"""Weekly vault check: lint + duplicates + Quicknotes triage + singleton tags + MOC drift.

Produces one report note at Research (RAW)/iofast/Weekly/<YYYY>-W<ww> Vault check.md.
Only --fix repairs MOC drift (same as lint --fix). Everything else is read-only.
"""

import json
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

from .dedupe import normalize_title
from .lint import lint_vault


def _iso_week(d: date | None = None) -> str:
    """Return YYYY-Www for the given date (default: today)."""
    d = d or date.today()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _find_quicknotes(vault: Path) -> list[dict]:
    """Find notes stuck in Quicknotes (Unorganized) with their titles."""
    qn = vault / "Quicknotes (Unorganized)"
    if not qn.is_dir():
        return []
    results = []
    for md_file in qn.glob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
            # Extract title from first H1 or filename
            title = md_file.stem
            for line in text.split("\n"):
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            results.append({"path": str(md_file), "title": title, "stem": md_file.stem})
        except OSError:
            continue
    return results


def _find_likely_duplicates(vault: Path) -> list[dict]:
    """Find notes with similar titles that might be duplicates."""
    seen = {}  # normalized_title -> list of (real_title, path)
    duplicates = []

    for md_file in vault.rglob("*.md"):
        if md_file.name.startswith(".") or "Research (RAW)" in str(md_file):
            continue
        title = md_file.stem
        norm = normalize_title(title)
        if norm in seen:
            seen[norm].append({"title": title, "path": str(md_file.relative_to(vault))})
        else:
            seen[norm] = [{"title": title, "path": str(md_file.relative_to(vault))}]

    for norm, entries in seen.items():
        if len(entries) > 1:
            duplicates.append({"normalized": norm, "notes": entries})

    return duplicates


def _find_singleton_tags(vault: Path) -> list[str]:
    """Find tags used only once across the vault."""
    from .scan import scan_vault
    scan = scan_vault(vault)
    vocab = scan.get("tag_vocabulary", {})
    return sorted(t for t, c in vocab.items() if c == 1)


def _suggest_folders(quicknotes: list[dict], vault: Path) -> list[dict]:
    """Use LLM to suggest folders for Quicknotes notes (one batched call)."""
    if not quicknotes:
        return []

    from .scan import scan_vault
    scan = scan_vault(vault)
    folders = scan.get("folders", [])

    # Build batch prompt
    notes_list = "\n".join(f"- {n['title']}" for n in quicknotes[:20])
    folders_list = ", ".join(folders[:30])

    prompt = f"""\
You are organizing notes in an Obsidian vault. For each note below, suggest the best existing folder.

Existing folders: {folders_list}

Notes to organize:
{notes_list}

Return ONLY valid JSON: [{{"title": "<note title>", "suggested_folder": "<folder name>"}}]
If a note clearly belongs in a new folder, suggest it but mark "new": true.
"""

    try:
        if __import__("shutil").which("claude"):
            llm_cwd = Path(os.environ.get("LOCALAPPDATA", ""), "iofast", "llm-cwd")
            llm_cwd.mkdir(parents=True, exist_ok=True)
            cmd = ["claude", "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config"]
            result = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                    timeout=120, cwd=str(llm_cwd))
            if result.returncode != 0:
                return []
            try:
                wrapper = json.loads(result.stdout)
                text = wrapper.get("result", result.stdout)
            except json.JSONDecodeError:
                text = result.stdout
        else:
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                return []
            import urllib.request
            payload = json.dumps({
                "model": "anthropic/claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 2048,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if not choices:
                return []
            text = choices[0].get("message", {}).get("content", "")

        # Parse response
        text = text.strip()
        if text.startswith("```"):
            first_nl = text.index("\n") if "\n" in text else 3
            text = text[first_nl + 1:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        suggestions = json.loads(text)
        return suggestions if isinstance(suggestions, list) else []
    except Exception:
        return []


def run_check(vault: Path, fix: bool = False, week: str | None = None) -> dict:
    """Run weekly vault check. Returns report dict and writes report note."""
    week_label = week or _iso_week()

    # Run lint
    lint_report = lint_vault(vault, fix=fix)

    # Find issues
    duplicates = _find_likely_duplicates(vault)
    quicknotes = _find_quicknotes(vault)
    singleton_tags = _find_singleton_tags(vault)

    # Suggest folders for Quicknotes (LLM call)
    folder_suggestions = _suggest_folders(quicknotes, vault)

    # Build report
    report = {
        "week": week_label,
        "vault_path": str(vault),
        "lint": lint_report,
        "likely_duplicates": duplicates,
        "quicknotes_count": len(quicknotes),
        "folder_suggestions": folder_suggestions,
        "singleton_tags": singleton_tags,
        "moc_drift": lint_report.get("moc_drift", []),
    }

    # Write report note
    weekly_dir = vault / "Research (RAW)" / "iofast" / "Weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    report_path = weekly_dir / f"{week_label} Vault check.md"

    lines = [
        "---",
        "tags: [iofast, vault-check]",
        f"created: {date.today().isoformat()}",
        "---",
        "",
        f"# {week_label} Vault Check",
        "",
        f"**Total notes:** {lint_report.get('total_notes', 0)}",
        f"**Broken links:** {len(lint_report.get('broken_links', []))}",
        f"**Orphans:** {len(lint_report.get('orphans', []))}",
        f"**Singleton tags:** {len(singleton_tags)}",
        f"**MOC drift:** {len(lint_report.get('moc_drift', []))}",
        "",
    ]

    if duplicates:
        lines.append("## Likely Duplicates")
        lines.append("")
        for dup in duplicates[:10]:
            titles = ", ".join(n["title"] for n in dup["notes"])
            lines.append(f"- **{dup['normalized']}**: {titles}")
        lines.append("")

    if quicknotes:
        lines.append(f"## Quicknotes ({len(quicknotes)} notes)")
        lines.append("")
        suggestion_map = {s.get("title", ""): s.get("suggested_folder", "?") for s in folder_suggestions}
        for qn in quicknotes[:20]:
            suggested = suggestion_map.get(qn["title"], "(no suggestion)")
            lines.append(f"- [[{qn['title']}]] → {suggested}")
        lines.append("")

    if singleton_tags:
        lines.append("## Singleton Tags")
        lines.append("")
        lines.append(", ".join(f"`{t}`" for t in singleton_tags[:30]))
        lines.append("")

    if lint_report.get("moc_drift"):
        lines.append("## MOC Drift")
        lines.append("")
        for drift in lint_report["moc_drift"][:10]:
            lines.append(f"- {drift}")
        lines.append("")

    if fix and lint_report.get("fixed_mocs"):
        lines.append("## Fixed MOCs")
        lines.append("")
        for m in lint_report["fixed_mocs"]:
            lines.append(f"- {m}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_path"] = str(report_path)

    return report