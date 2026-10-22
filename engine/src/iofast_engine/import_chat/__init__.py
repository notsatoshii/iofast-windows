"""Chat history import: ChatGPT and Claude export archives.

Each conversation becomes one markdown file with title, date, and messages
with speaker labels. Raw copies go to Research (RAW)/AI chats/<source>/.
NO UNTRUSTED banner — these are Eric's own chats.

Module layout:
- chatgpt.py: parse ChatGPT conversations.json (mapping tree format)
- claude.py: parse Claude conversations.json (chat_messages list format)
- writer.py: write conversation markdown + vault filing
- import_rpc.py: RPC handler functions for import.chatgpt / import.claude
"""