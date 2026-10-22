"""Watch list: monitor URLs for new content on a schedule.

Stored in ``%APPDATA%/iofast/watch.json`` (Windows) or
``~/Library/Application Support/iofast/watch.json`` (macOS). Each entry
tracks last-seen state so repeated runs only surface genuinely new items.

Module layout:
- store.py: persistence layer (load/save/list/add/remove)
- checker.py: per-kind change detection (YouTube, GitHub, web RSS/sitemap/hash, social)
- runner.py: orchestrates check + grab + optional vault filing
- watch_rpc.py: RPC handler functions for watch.add/list/remove/run
"""