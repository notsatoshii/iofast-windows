# gate.ps1 — Windows equivalent of `make gate`.
# Runs engine pytest, cargo test, frontend build, and verifies the frozen sidecar.
# Usage: .\scripts\windows\gate.ps1 [-Python <path-to-python>]
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$engineDir = Join-Path $repoRoot "engine"
$appDir = Join-Path $repoRoot "app"
$tauriDir = Join-Path $appDir "src-tauri"

if (-not $Python) {
    $venvPy = Join-Path $engineDir ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) {
        $Python = $venvPy
    } else {
        Write-Error "No python found. Pass -Python <path> or create engine\.venv first."
        exit 1
    }
}

# 1. Engine tests
Write-Host "=== Engine pytest ===" -ForegroundColor Cyan
Push-Location $engineDir
try {
    $env:PYTHONPATH = "src"
    & $Python -m pytest tests -q
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Engine pytest failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

# 2. Frozen sidecar exists
Write-Host "=== Checking frozen sidecar ===" -ForegroundColor Cyan
$frozenExe = Join-Path $engineDir "dist\iofast\iofast.exe"
if (-not (Test-Path $frozenExe)) {
    Write-Error "Frozen sidecar not found at $frozenExe — run .\scripts\windows\freeze.ps1 first."
    exit 1
}

# 3. Cargo test
Write-Host "=== Cargo test ===" -ForegroundColor Cyan
Push-Location $tauriDir
try {
    if (-not (Test-Path "target")) { New-Item -ItemType Directory -Path "target" | Out-Null }
    $logFile = Join-Path "target" "cargo-test.log"
    cargo test 2>&1 | Tee-Object -FilePath $logFile
    $logContent = Get-Content $logFile -Raw
    if ($logContent -notmatch 'test result: ok\. [1-9][0-9]* passed') {
        Write-Error "Cargo tests did not report any passing tests — check $logFile"
        exit 1
    }
} finally {
    Pop-Location
}

# 4. Frontend build
Write-Host "=== Frontend build ===" -ForegroundColor Cyan
Push-Location $appDir
try {
    pnpm run build
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Frontend build failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

# 5. Verify frozen artifact provenance
Write-Host "=== Verifying frozen artifact ===" -ForegroundColor Cyan
Push-Location $engineDir
try {
    $env:PYTHONPATH = "src"
    & $Python packaging\verify_frozen.py dist\iofast\iofast.exe
    if ($LASTEXITCODE -ne 0) {
        Write-Error "verify_frozen.py failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host "Gate passed." -ForegroundColor Green