"""Parse ChatGPT export archive.

ChatGPT exports contain conversations.json with a mapping tree structure:
{
  "<uuid>": {
    "title": "...",
    "create_time": 1234567890.0,
    "mapping": {
      "<node_id>": {
        "id": "<node_id>",
        "message": {
          "author": {"role": "user"|"assistant"|"system"},
          "content": {"parts": ["text..."]} | null
        },
        "children": ["<child_node_id>", ...],
        "parent": "<parent_node_id>" | null
      }
    }
  }
}

We walk the tree following children from the root, taking only the final
branch (deepest leaf path). System messages are skipped.
"""

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def _walk_final_branch(mapping: dict) -> list[dict]:
    """Walk the conversation tree, returning messages along the final branch."""
    # Find root node(s): nodes with no parent
    roots = [nid for nid, node in mapping.items() if node.get("parent") is None]
    if not roots:
        return []

    # Start from the first root and follow children greedily (last child = final branch)
    messages = []
    current = roots[0]
    visited = set()

    while current and current not in visited:
        visited.add(current)
        node = mapping.get(current)
        if not node:
            break

        msg = node.get("message")
        if msg and msg.get("author", {}).get("role") in ("user", "assistant"):
            content = msg.get("content")
            text = ""
            if isinstance(content, dict):
                parts = content.get("parts", [])
                text = "".join(p for p in parts if isinstance(p, str))
            elif isinstance(content, str):
                text = content
            if text.strip():
                messages.append({
                    "role": msg["author"]["role"],
                    "text": text.strip(),
                })

        children = node.get("children", [])
        if children:
            current = children[-1]  # final branch
        else:
            break

    return messages


def parse_chatgpt_export(zip_path: str | Path, since: str | None = None,
                         min_messages: int = 4, limit: int | None = None) -> list[dict]:
    """Parse ChatGPT export zip. Returns list of conversation dicts.

    Each dict: {id, title, date, messages: [{role, text}], message_count, char_count}
    Filters by --since (YYYY-MM-DD), --min-messages, and --limit.
    """
    zip_path = Path(zip_path)
    conversations = []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Find conversations.json in the archive
            names = zf.namelist()
            conv_file = None
            for name in names:
                if name.endswith("conversations.json"):
                    conv_file = name
                    break
            if conv_file is None:
                raise ValueError("conversations.json not found in archive")

            with zf.open(conv_file) as f:
                data = json.load(f)
    except (zipfile.BadZipFile, KeyError) as e:
        raise ValueError(f"invalid ChatGPT export: {e}")

    if not isinstance(data, dict):
        raise ValueError("unexpected ChatGPT export format")

    since_dt = None
    if since:
        try:
            since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    count = 0
    for conv_id, conv in data.items():
        if not isinstance(conv, dict):
            continue

        title = conv.get("title", "Untitled")
        create_time = conv.get("create_time")
        date_str = ""
        conv_dt = None
        if create_time:
            try:
                conv_dt = datetime.fromtimestamp(float(create_time), tz=timezone.utc)
                date_str = conv_dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError, OSError):
                pass

        # Filter by --since
        if since_dt and conv_dt and conv_dt < since_dt:
            continue

        mapping = conv.get("mapping", {})
        if not isinstance(mapping, dict):
            continue

        messages = _walk_final_branch(mapping)
        msg_count = len(messages)
        char_count = sum(len(m["text"]) for m in messages)

        # Trivial skip: too few messages or too short
        if msg_count < min_messages:
            continue
        if char_count < 300:
            continue

        conversations.append({
            "id": conv_id,
            "title": title,
            "date": date_str,
            "messages": messages,
            "message_count": msg_count,
            "char_count": char_count,
            "source": "chatgpt",
        })
        count += 1
        if limit and count >= limit:
            break

    return conversations