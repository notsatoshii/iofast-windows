"""Watch runner: orchestrate check + grab + optional vault filing.

One failing entry never stops others. Results logged to Research
(RAW)/iofast/log.md when filing is enabled.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .checker import check


def _default_out_dir() -> Path:
    """Library output root for watch grabs."""
    import platform
    if platform.system() == "Windows":
        import os
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return Path(base) / "iofast" / "library" / "watch"
    return Path.home() / "Library" / "Application Support" / "iofast" / "library" / "watch"


def run_watch(if_due: bool = False, backfill: int = 0, emit=None) -> dict:
    """Run all due watch entries. Returns summary dict."""
    entries = store.get_due_entries(if_due=if_due)
    if not entries:
        return {"checked": 0, "new_items": 0, "errors": 0, "results": []}

    total_new = 0
    total_errors = 0
    results = []

    for entry in entries:
        entry_id = entry["id"]
        kind = entry.get("kind", "web")
        url = entry["url"]

        if emit:
            emit({"type": "progress", "message": f"checking {kind}: {url}"})

        try:
            new_items = check(entry, emit=emit)

            # Backfill: on first run, allow capturing N items
            if not entry.get("last_seen", {}).get("initialized") and backfill > 0:
                new_items = new_items[:backfill]
            elif not entry.get("last_seen", {}).get("initialized"):
                new_items = []

            # Grab each new item
            grabbed = 0
            for item in new_items:
                item_url = item.get("url", url)
                try:
                    out_dir = _default_out_dir() / entry_id / item.get("id", "unknown")
                    out_dir.mkdir(parents=True, exist_ok=True)

                    from ..sources import GrabOptions, detect
                    source = detect(item_url)
                    opts = GrabOptions(mode="page", limit=1)
                    grab_result = asyncio.run(source.grab(item_url, out_dir, opts, emit or (lambda e: None)))

                    # Vault filing if entry has --file
                    if entry.get("file") and (grab_result.ok + grab_result.thin) > 0:
                        try:
                            from ..vault.vault_rpc import vault_file
                            vault_file({"run_dir": str(out_dir)}, emit=emit)
                        except Exception as ve:
                            if emit:
                                emit({"type": "warning", "message": f"vault filing failed: {ve}"})

                    grabbed += 1
                except Exception as ge:
                    if emit:
                        emit({"type": "warning", "message": f"grab failed for {item_url}: {ge}"})
                    total_errors += 1

            # Update entry timestamp
            store.update_entry(entry_id, last_run=datetime.now(timezone.utc).isoformat())

            result = {
                "id": entry_id,
                "url": url,
                "kind": kind,
                "new_items": len(new_items),
                "grabbed": grabbed,
            }
            results.append(result)
            total_new += len(new_items)

            if emit:
                emit({
                    "type": "progress",
                    "message": f"{kind}: {len(new_items)} new, {grabbed} grabbed",
                })

        except Exception as e:
            total_errors += 1
            results.append({
                "id": entry_id,
                "url": url,
                "kind": kind,
                "error": str(e),
            })
            if emit:
                emit({"type": "warning", "message": f"watch entry failed: {e}"})

    return {
        "checked": len(entries),
        "new_items": total_new,
        "errors": total_errors,
        "results": results,
    }