"""RPC handlers for vault operations.

Plain functions with (params, emit) -> dict signature for:
- vault.status: check vault availability
- vault.file: file a capture
- vault.lint: lint the vault

Documented in docs/RPC.md. Integrated by rpc.py importing these functions.
"""

from pathlib import Path


def vault_status(params: dict, emit=None) -> dict:
    """Check vault availability and basic stats."""
    from .detect import detect_vaults
    from .scan import scan_vault

    vaults = detect_vaults()
    if not vaults:
        return {"available": False, "vaults": []}

    results = []
    for v in vaults:
        try:
            scan = scan_vault(v["path"])
            results.append({
                "path": v["path"],
                "open": v.get("open", False),
                "folders": len(scan["folders"]),
                "notes": len(scan["notes"]),
                "tags": len(scan["tag_vocabulary"]),
            })
        except (ValueError, OSError) as e:
            results.append({
                "path": v["path"],
                "open": v.get("open", False),
                "error": str(e),
            })

    return {"available": True, "vaults": results}


def vault_file(params: dict, emit=None) -> dict:
    """File a capture into the vault."""
    run_dir = params.get("run_dir")
    if not isinstance(run_dir, str) or not run_dir:
        raise ValueError("run_dir is required and must be a string")

    dry_run = params.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")

    vault_path = params.get("vault")
    backend = params.get("backend")

    from .filer import file_capture

    def _emit(event):
        if emit:
            emit(event)

    return file_capture(
        run_dir,
        vault_path=vault_path,
        dry_run=dry_run,
        backend=backend,
        emit=_emit,
    )


def vault_lint(params: dict, emit=None) -> dict:
    """Lint the vault."""
    vault_path = params.get("vault")
    fix = params.get("fix", False)
    if not isinstance(fix, bool):
        raise ValueError("fix must be a boolean")

    if vault_path is None:
        from .detect import detect_vaults
        vaults = detect_vaults()
        if not vaults:
            raise RuntimeError("no Obsidian vaults found")
        open_vaults = [v for v in vaults if v.get("open")]
        vault_path = open_vaults[0]["path"] if open_vaults else vaults[0]["path"]

    from .lint import lint_vault
    return lint_vault(vault_path, fix=fix)