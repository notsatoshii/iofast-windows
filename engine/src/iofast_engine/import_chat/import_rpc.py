"""RPC handlers for chat history import.

Plain functions with (params, emit) -> dict signature for:
- import.chatgpt: import ChatGPT export archive
- import.claude: import Claude export archive

Documented in docs/RPC.md. Integrated by rpc.py importing these functions.
"""

from pathlib import Path


def import_chatgpt(params: dict, emit=None) -> dict:
    """Import ChatGPT export archive."""
    zip_path = params.get("path")
    if not isinstance(zip_path, str) or not zip_path:
        raise ValueError("path is required and must be a non-empty string")
    if not Path(zip_path).exists():
        raise ValueError(f"file not found: {zip_path}")

    dry_run = params.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    since = params.get("since")
    if not (since is None or isinstance(since, str)):
        raise ValueError("since must be a string or null")
    limit = params.get("limit")
    if not (limit is None or (isinstance(limit, int) and limit > 0)):
        raise ValueError("limit must be a positive integer or null")
    min_messages = params.get("min_messages", 4)
    if not isinstance(min_messages, int) or min_messages < 0:
        raise ValueError("min_messages must be a non-negative integer")

    from .chatgpt import parse_chatgpt_export
    from .writer import import_conversations

    def _emit(event):
        if emit:
            emit(event)

    conversations = parse_chatgpt_export(
        zip_path, since=since, min_messages=min_messages, limit=limit,
    )

    # Progress file for resumability
    progress_file = Path(zip_path).parent / ".iofast-import-chatgpt-progress.json"

    result = import_conversations(
        conversations, source="chatgpt",
        dry_run=dry_run, progress_file=progress_file, emit=_emit,
    )
    result["conversations_found"] = len(conversations)
    return result


def import_claude(params: dict, emit=None) -> dict:
    """Import Claude export archive."""
    zip_path = params.get("path")
    if not isinstance(zip_path, str) or not zip_path:
        raise ValueError("path is required and must be a non-empty string")
    if not Path(zip_path).exists():
        raise ValueError(f"file not found: {zip_path}")

    dry_run = params.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    since = params.get("since")
    if not (since is None or isinstance(since, str)):
        raise ValueError("since must be a string or null")
    limit = params.get("limit")
    if not (limit is None or (isinstance(limit, int) and limit > 0)):
        raise ValueError("limit must be a positive integer or null")
    min_messages = params.get("min_messages", 4)
    if not isinstance(min_messages, int) or min_messages < 0:
        raise ValueError("min_messages must be a non-negative integer")

    from .claude import parse_claude_export
    from .writer import import_conversations

    def _emit(event):
        if emit:
            emit(event)

    conversations = parse_claude_export(
        zip_path, since=since, min_messages=min_messages, limit=limit,
    )

    progress_file = Path(zip_path).parent / ".iofast-import-claude-progress.json"

    result = import_conversations(
        conversations, source="claude",
        dry_run=dry_run, progress_file=progress_file, emit=_emit,
    )
    result["conversations_found"] = len(conversations)
    return result