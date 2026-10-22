# freeze.ps1 — Windows equivalent of `make freeze`.
# Builds the frozen PyInstaller sidecar into engine/dist/iofast/.
# Usage: .\scripts\windows\freeze.ps1 [-Python <path-to-python>]
param(
    [string]$Python = ""
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

$pyinstaller = Join-Path (Split-Path -Parent $Python) "pyinstaller.exe"
if (-not (Test-Path $pyinstaller)) {
    # Try Scripts/ subdir (some venv layouts)
    $pyinstaller = Join-Path (Split-Path -Parent $Python) "Scripts\pyinstaller.exe"
}
if (-not (Test-Path $pyinstaller)) {
    Write-Error "pyinstaller not found beside $Python — install the [build] extra in that venv."
    exit 1
}

Push-Location $engineDir
try {
    & $pyinstaller --noconfirm packaging\iofast.spec
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pyinstaller failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }

    $verifyPy = $Python
    $env:PYTHONPATH = "src"
    & $verifyPy packaging\verify_frozen.py dist\iofast\iofast.exe
    if ($LASTEXITCODE -ne 0) {
        Write-Error "verify_frozen.py failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host "Freeze complete: engine\dist\iofast\iofast.exe verified."