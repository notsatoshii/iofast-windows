# gate.ps1: Windows equivalent of `make gate`. The merge gate.
# Runs engine pytest, freezes + verifies the sidecar (freeze.ps1), then cargo
# test and the frontend build.
# Usage: .\scripts\windows\gate.ps1 [-Python <path-to-python>] [-SkipFreeze]
#   -SkipFreeze reuses an existing engine\dist freeze and only re-verifies it.
param(
    [string]$Python = "",
    [switch]$SkipFreeze
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

# 2. Freeze + verify the sidecar. cargo's tauri build script needs engine\dist,
# and a stale venv or stale freeze must fail here, not after the merge.
$frozenExe = Join-Path $engineDir "dist\iofast\iofast.exe"
if ($SkipFreeze) {
    Write-Host "=== Verifying existing frozen sidecar (-SkipFreeze) ===" -ForegroundColor Cyan
    if (-not (Test-Path $frozenExe)) {
        Write-Error "Frozen sidecar not found at $frozenExe. Run without -SkipFreeze."
        exit 1
    }
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
} else {
    Write-Host "=== Freeze (freeze.ps1) ===" -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "freeze.ps1") -Python $Python
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# 3. Frontend build (first: tauri embeds app\dist at compile time)
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

# 4. Cargo test
Write-Host "=== Cargo test ===" -ForegroundColor Cyan
Push-Location $tauriDir
try {
    if (-not (Test-Path "target")) { New-Item -ItemType Directory -Path "target" | Out-Null }
    $logFile = Join-Path "target" "cargo-test.log"
    # cargo prints progress on stderr; under "Stop" Windows PowerShell 5.1 would
    # treat the first stderr line as a terminating error.
    $ErrorActionPreference = "Continue"
    cargo test 2>&1 | Tee-Object -FilePath $logFile
    $cargoExit = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($cargoExit -ne 0) {
        Write-Error "cargo test failed with exit code $cargoExit"
        exit $cargoExit
    }
    $logContent = Get-Content $logFile -Raw
    if ($logContent -notmatch 'test result: ok\. [1-9][0-9]* passed') {
        Write-Error "Cargo tests did not report any passing tests. Check $logFile"
        exit 1
    }
} finally {
    Pop-Location
}

Write-Host "Gate passed." -ForegroundColor Green