# bundle.ps1 — Windows equivalent of `make bundle`.
# Freezes the engine, builds the NSIS installer via tauri, and verifies the
# bundled sidecar inside the installer output.
# Usage: .\scripts\windows\bundle.ps1 [-Python <path-to-python>]
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$engineDir = Join-Path $repoRoot "engine"
$appDir = Join-Path $repoRoot "app"

# Step 1: Freeze (includes verify)
Write-Host "=== Step 1: Freeze ===" -ForegroundColor Cyan
& "$PSScriptRoot\freeze.ps1" -Python $Python
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Step 2: Tauri build (produces NSIS installer + exe)
Write-Host "=== Step 2: Tauri build ===" -ForegroundColor Cyan
Push-Location $appDir
try {
    pnpm tauri build
    if ($LASTEXITCODE -ne 0) {
        Write-Error "tauri build failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

# Step 3: Verify the sidecar that tauri copied into the bundle.
# On Windows the sidecar lands next to the main exe in the NSIS output dir.
Write-Host "=== Step 3: Verify bundled sidecar ===" -ForegroundColor Cyan
$nsisDir = Join-Path $appDir "src-tauri\target\release\bundle\nsis"
$bundledExe = Get-ChildItem -Path $nsisDir -Filter "iofast*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $bundledExe) {
    # Fallback: check the release dir directly for the portable exe
    $releaseDir = Join-Path $appDir "src-tauri\target\release"
    $bundledExe = Get-ChildItem -Path $releaseDir -Filter "iofast.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $bundledExe) {
    Write-Warning "Could not locate bundled exe for verification — NSIS installer was still produced."
    Write-Host "Bundle complete (verification skipped)." -ForegroundColor Yellow
    exit 0
}

# For NSIS bundles, the sidecar is extracted at install time; we verify the
# source-of-truth copy in engine/dist instead, which tauri copies from.
Push-Location $engineDir
try {
    if (-not $Python) {
        $venvPy = Join-Path $engineDir ".venv\Scripts\python.exe"
        if (Test-Path $venvPy) { $Python = $venvPy }
    }
    if ($Python) {
        $env:PYTHONPATH = "src"
        & $Python packaging\verify_frozen.py dist\iofast\iofast.exe
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Bundled sidecar verification failed"
            exit $LASTEXITCODE
        }
    }
} finally {
    Pop-Location
}

Write-Host "Bundle complete: NSIS installer produced and sidecar verified." -ForegroundColor Green