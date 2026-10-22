"""Parse Claude export archive.

Claude exports contain conversations.json with a flat list structure:
[
  {
    "uuid": "...",
    "name": "Conversation title",
    "created_at": "2025-01-15T10:30:00Z",
    "chat_messages": [
      {
        "sender": "human" | "assistant",
        "text": "message content..."
      }
    ]
  }
]
"""

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def parse_claude_export(zip_path: str | Path, since: str | None = None,
                        min_messages: int = 4, limit: int | None = None) -> list[dict]:
    """Parse Claude export zip. Returns list of conversation dicts.

    Each dict: {id, title, date, messages: [{role, text}], message_count, char_count}
    Filters by --since (YYYY-MM-DD), --min-messages, and --limit.
    """
    zip_path = Path(zip_path)
    conversations = []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
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
        raise ValueError(f"invalid Claude export: {e}")

    # Claude format is a list of conversations
    if isinstance(data, dict):
        # Some exports wrap in {"conversations": [...]}
        data = data.get("conversations", [])
    if not isinstance(data, list):
        raise ValueError("unexpected Claude export format")

    since_dt = None
    if since:
        try:
            since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    count = 0
    for conv in data:
        if not isinstance(conv, dict):
            continue

        conv_id = conv.get("uuid", conv.get("id", ""))
        title = conv.get("name", conv.get("title", "Untitled"))
        created = conv.get("created_at", conv.get("created_at_utc", ""))
        date_str = ""
        conv_dt = None

        if created:
            try:
                if isinstance(created, (int, float)):
                    conv_dt = datetime.fromtimestamp(float(created), tz=timezone.utc)
                else:
                    # ISO format string
                    created_str = str(created).replace("Z", "+00:00")
                    conv_dt = datetime.fromisoformat(created_str)
                    if conv_dt.tzinfo is None:
                        conv_dt = conv_dt.replace(tzinfo=timezone.utc)
                date_str = conv_dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError, OSError):
                pass

        # Filter by --since
        if since_dt and conv_dt and conv_dt < since_dt:
            continue

        chat_messages = conv.get("chat_messages", conv.get("messages", []))
        if not isinstance(chat_messages, list):
            continue

        messages = []
        for msg in chat_messages:
            if not isinstance(msg, dict):
                continue
            sender = msg.get("sender", msg.get("role", ""))
            text = msg.get("text", msg.get("content", ""))
            if not isinstance(text, str):
                continue
            # Map sender to role
            role = "user" if sender in ("human", "user") else "assistant"
            if text.strip():
                messages.append({"role": role, "text": text.strip()})

        msg_count = len(messages)
        char_count = sum(len(m["text"]) for m in messages)

        # Trivial skip
        if msg_count < min_messages:
            continue
        if char_count < 300:
            continue

        conversations.append({
            "id": str(conv_id),
            "title": title,
            "date": date_str,
            "messages": messages,
            "message_count": msg_count,
            "char_count": char_count,
            "source": "claude",
        })
        count += 1
        if limit and count >= limit:
            break

    return conversations