"""Create proper test fixtures for chat export tests."""
import json
import zipfile
from pathlib import Path

fixture_dir = Path("tests/fixtures/chat_exports")
fixture_dir.mkdir(parents=True, exist_ok=True)

# --- ChatGPT export fixture ---
chatgpt_data = {
    "conv-001-aaa-bbb": {
        "title": "Python JSON Parsing Help",
        "create_time": 1729600000.0,
        "mapping": {
            "root-node": {
                "id": "root-node",
                "message": None,
                "children": ["user-msg-1"],
                "parent": None,
            },
            "user-msg-1": {
                "id": "user-msg-1",
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": [
                        "Hello, can you help me with Python? I am trying to read a JSON file "
                        "and parse it into a dictionary so I can access nested values. "
                        "What is the best approach for handling large files efficiently?"
                    ]},
                },
                "children": ["asst-msg-1"],
                "parent": "root-node",
            },
            "asst-msg-1": {
                "id": "asst-msg-1",
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"parts": [
                        "Of course! I would be happy to help you with Python JSON parsing. "
                        "For reading JSON files, you can use the built-in json module. "
                        "Here is an example: import json; then use json.load(open('file.json')) "
                        "to parse the entire file into a Python dictionary. For large files, "
                        "consider using ijson for streaming parsing instead of loading everything into memory."
                    ]},
                },
                "children": ["user-msg-2"],
                "parent": "user-msg-1",
            },
            "user-msg-2": {
                "id": "user-msg-2",
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": [
                        "That is very helpful, thank you! How do I handle cases where the JSON "
                        "file might have encoding issues or be malformed? Should I wrap the "
                        "parsing in a try-except block, and what specific exceptions should I catch?"
                    ]},
                },
                "children": ["asst-msg-2"],
                "parent": "asst-msg-1",
            },
            "asst-msg-2": {
                "id": "asst-msg-2",
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"parts": [
                        "Yes, always wrap JSON parsing in error handling. Catch json.JSONDecodeError "
                        "for malformed JSON, UnicodeDecodeError for encoding problems, and IOError "
                        "for file access issues. You can specify encoding explicitly: "
                        "open('file.json', encoding='utf-8'). For robustness, validate the parsed "
                        "structure before accessing nested keys using .get() methods."
                    ]},
                },
                "children": [],
                "parent": "user-msg-2",
            },
        },
    },
    "conv-002-ccc-ddd": {
        "title": "Short Chat",
        "create_time": 1729500000.0,
        "mapping": {
            "root": {
                "id": "root",
                "message": None,
                "children": ["u1"],
                "parent": None,
            },
            "u1": {
                "id": "u1",
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["Hi"]},
                },
                "children": [],
                "parent": "root",
            },
        },
    },
}

chatgpt_zip = fixture_dir / "chatgpt_export.zip"
with zipfile.ZipFile(chatgpt_zip, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("conversations.json", json.dumps(chatgpt_data, indent=2))
print(f"Created {chatgpt_zip}")

# --- Claude export fixture ---
claude_data = [
    {
        "uuid": "claude-conv-001",
        "name": "Python Project Structure Discussion",
        "created_at": "2024-10-22T10:00:00Z",
        "chat_messages": [
            {
                "sender": "human",
                "text": "What is the best way to structure a Python project? I want to follow "
                        "modern best practices for package layout, testing, and configuration management.",
            },
            {
                "sender": "assistant",
                "text": "A good Python project structure includes a src/ layout with your package "
                        "under src/mypackage/, a pyproject.toml for build configuration, a tests/ "
                        "directory mirroring your source structure, and clear separation of concerns. "
                        "Would you like specific recommendations for your use case?",
            },
            {
                "sender": "human",
                "text": "Yes please, give me a detailed layout with examples. I am building a "
                        "CLI tool that processes data files and generates reports.",
            },
            {
                "sender": "assistant",
                "text": "Here is a recommended layout: src/mytool/__init__.py for version info, "
                        "src/mytool/core.py for main logic, src/mytool/cli.py for argument parsing, "
                        "tests/test_core.py for unit tests, pyproject.toml with build-system config "
                        "and entry points, README.md with usage examples, and LICENSE. Each module "
                        "should have a single responsibility and be independently testable.",
            },
        ],
    },
    {
        "uuid": "claude-conv-002-trivial",
        "name": "Quick Question",
        "created_at": "2024-10-21T08:00:00Z",
        "chat_messages": [
            {"sender": "human", "text": "Hi"},
            {"sender": "assistant", "text": "Hello!"},
        ],
    },
]

claude_zip = fixture_dir / "claude_export.zip"
with zipfile.ZipFile(claude_zip, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("conversations.json", json.dumps(claude_data, indent=2))
print(f"Created {claude_zip}")

# Verify
for zp in [chatgpt_zip, claude_zip]:
    with zipfile.ZipFile(zp) as zf:
        print(f"  {zp.name}: {zf.namelist()}")