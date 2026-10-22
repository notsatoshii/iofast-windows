"""RPC handlers for watch list operations.

Plain functions with (params, emit) -> dict signature for:
- watch.add: add a URL to the watch list
- watch.list: list all watch entries
- watch.remove: remove an entry by id
- watch.run: run due checks

Documented in docs/RPC.md. Integrated by rpc.py importing these functions.
"""


def watch_add(params: dict, emit=None) -> dict:
    """Add a URL to the watch list."""
    url = params.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("url is required and must be a non-empty string")
    every = params.get("every", "daily")
    if every not in ("daily", "weekly"):
        raise ValueError("every must be 'daily' or 'weekly'")
    file = params.get("file", False)
    if not isinstance(file, bool):
        raise ValueError("file must be a boolean")
    limit = params.get("limit", 10)
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")

    from .store import add
    entry = add(url, every=every, file=file, limit=limit)
    return {"added": True, "entry": entry}


def watch_list(params: dict, emit=None) -> dict:
    """List all watch entries."""
    from .store import list_entries
    entries = list_entries()
    return {"entries": entries, "count": len(entries)}


def watch_remove(params: dict, emit=None) -> dict:
    """Remove a watch entry by id prefix."""
    entry_id = params.get("id")
    if not isinstance(entry_id, str) or not entry_id:
        raise ValueError("id is required and must be a non-empty string")

    from .store import remove
    found = remove(entry_id)
    return {"removed": found, "id": entry_id}


def watch_run(params: dict, emit=None) -> dict:
    """Run due watch checks."""
    if_due = params.get("if_due", False)
    if not isinstance(if_due, bool):
        raise ValueError("if_due must be a boolean")
    backfill = params.get("backfill", 0)
    if not isinstance(backfill, int) or backfill < 0:
        raise ValueError("backfill must be a non-negative integer")

    from .runner import run_watch

    def _emit(event):
        if emit:
            emit(event)

    return run_watch(if_due=if_due, backfill=backfill, emit=_emit)