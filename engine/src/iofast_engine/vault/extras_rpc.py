"""RPC handlers for vault extras: ask, check, digest.

Plain functions with (params, emit) -> dict signature.
Documented in docs/RPC.md. Integrated by rpc.py importing these functions.
"""

from pathlib import Path


def _resolve_vault(vault_param: str | None) -> Path:
    """Resolve vault path from param or auto-detect."""
    if vault_param:
        return Path(vault_param)
    from .detect import detect_vaults
    vaults = detect_vaults()
    if not vaults:
        raise RuntimeError("no Obsidian vaults found")
    open_vaults = [v for v in vaults if v.get("open")]
    return Path(open_vaults[0]["path"] if open_vaults else vaults[0]["path"])


def vault_ask(params: dict, emit=None) -> dict:
    """Ask a question over the vault notes."""
    question = params.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question is required and must be a non-empty string")

    save = params.get("save", False)
    if not isinstance(save, bool):
        raise ValueError("save must be a boolean")

    vault_path = _resolve_vault(params.get("vault"))

    from .ask import ask_vault
    return ask_vault(vault_path, question.strip(), save=save)


def vault_check(params: dict, emit=None) -> dict:
    """Run weekly vault health check."""
    fix = params.get("fix", False)
    if not isinstance(fix, bool):
        raise ValueError("fix must be a boolean")

    week = params.get("week")
    if week is not None and not isinstance(week, str):
        raise ValueError("week must be a string (YYYY-Www)")

    vault_path = _resolve_vault(params.get("vault"))

    from .check import run_check
    return run_check(vault_path, fix=fix, week=week)


def digest_make(params: dict, emit=None) -> dict:
    """Generate weekly research digest."""
    week = params.get("week")
    if week is not None and not isinstance(week, str):
        raise ValueError("week must be a string (YYYY-Www)")

    vault_path = _resolve_vault(params.get("vault"))

    from .digest import make_digest
    return make_digest(vault_path, week=week)