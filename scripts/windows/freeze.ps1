# freeze.ps1: Windows equivalent of `make freeze`. The only Windows freeze script.
# Builds the frozen PyInstaller sidecar into engine\dist\iofast\ and verifies it.
#
# Steps:
#   1. Sync the venv with the engine's runtime + dev + build deps (skip with -NoSync)
#   2. Check every runtime dependency imports (fail loud if not)
#   3. PyInstaller with packaging\iofast.spec
#   4. packaging\verify_frozen.py against the frozen exe (full provenance check;
#      needs a committed tree, see iofast.spec)
#
# Usage: .\scripts\windows\freeze.ps1 [-Python <path-to-python>] [-NoSync]
# gate.ps1, bundle.ps1 and the Windows CI job all call this script.
param(
    [string]$Python = "",
    [switch]$NoSync
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$engineDir = Join-Path $repoRoot "engine"

if (-not $Python) {
    $venvPy = Join-Path $engineDir ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) {
        $Python = $venvPy
    } else {
        Write-Error "No python found. Pass -Python <path> or create engine\.venv first."
        exit 1
    }
}

Push-Location $engineDir
try {
    if (-not $NoSync) {
        Write-Host "=== Syncing venv with pyproject.toml deps ===" -ForegroundColor Cyan
        & $Python -m pip install -e ".[dev,build]" --quiet
        if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed with exit code $LASTEXITCODE"; exit $LASTEXITCODE }
    }

    Write-Host "=== Verifying runtime dependencies ===" -ForegroundColor Cyan
    $verifyFile = Join-Path ([System.IO.Path]::GetTempPath()) "iofast-verify-deps.py"
@'
import sys
deps = ["scrapling", "trafilatura", "markdownify", "patchright",
        "cssselect", "lxml", "yt_dlp", "httpx",
        "faster_whisper", "ctranslate2", "av", "tokenizers",
        "onnxruntime", "huggingface_hub"]
missing = []
for d in deps:
    try:
        __import__(d)
    except ImportError as e:
        missing.append(f"{d}: {e}")
if missing:
    print("MISSING runtime deps:", file=sys.stderr)
    for m in missing:
        print(f"  {m}", file=sys.stderr)
    sys.exit(1)
print("All runtime deps OK")
'@ | Set-Content -Path $verifyFile -Encoding UTF8
    & $Python -I $verifyFile
    $depsResult = $LASTEXITCODE
    Remove-Item $verifyFile -ErrorAction SilentlyContinue
    if ($depsResult -ne 0) { Write-Error "Runtime dependency check failed"; exit 1 }

    Write-Host "=== Running PyInstaller ===" -ForegroundColor Cyan
    & $Python -m PyInstaller --noconfirm packaging\iofast.spec
    if ($LASTEXITCODE -ne 0) { Write-Error "pyinstaller failed with exit code $LASTEXITCODE"; exit $LASTEXITCODE }

    Write-Host "=== Verifying frozen bundle ===" -ForegroundColor Cyan
    $env:PYTHONPATH = "src"
    & $Python packaging\verify_frozen.py dist\iofast\iofast.exe
    if ($LASTEXITCODE -ne 0) { Write-Error "verify_frozen.py failed with exit code $LASTEXITCODE"; exit $LASTEXITCODE }
} finally {
    Pop-Location
}

Write-Host "Freeze complete: engine\dist\iofast\iofast.exe verified." -ForegroundColor Green
