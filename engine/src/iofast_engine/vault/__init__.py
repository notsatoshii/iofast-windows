"""Obsidian vault filing for iofast captures (step 6).

Files any iofast capture into Eric's Obsidian vault: raw immutable copy,
LLM-classified source note with tags/links/entities, MOC update, log append.
Follows the "LLM wiki" pattern — the engine writes files deterministically
from structured JSON the LLM returns (no tools in the LLM step).
"""

from .scan import scan_vault
from .filer import file_capture
from .lint import lint_vault
from .detect import detect_vaults

__all__ = ["scan_vault", "file_capture", "lint_vault", "detect_vaults"]