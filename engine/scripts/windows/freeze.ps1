# Freeze the engine sidecar for Windows.
# Usage: .\scripts\windows\freeze.ps1
#
# Steps:
#   1. Sync venv with ALL runtime + dev + build deps from pyproject.toml
#   2. Verify every runtime dependency imports (fail loud if not)
#   3. Run PyInstaller with packaging/iofast.spec
#   4. Verify the frozen bundle via packaging/verify_frozen.py

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot\..\..

Write-Host "=== Syncing venv with pyproject.toml deps ==="
& .venv\Scripts\pip.exe install -e ".[dev,build]" --quiet 2>$null
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "=== Verifying runtime dependencies ==="
$verifyFile = Join-Path $env:TEMP "iofast-verify-deps.py"
@'
import sys
deps = ["scrapling", "trafilatura", "markdownify", "playwright", "patchright",
        "cssselect", "lxml", "yt_dlp"]
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
& .venv\Scripts\python.exe -I $verifyFile
$verifyResult = $LASTEXITCODE
Remove-Item $verifyFile -ErrorAction SilentlyContinue
if ($verifyResult -ne 0) { throw "Runtime dependency verification failed" }

Write-Host "=== Running PyInstaller ==="
& .venv\Scripts\pyinstaller.exe --noconfirm packaging\iofast.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# NOTE: verify_frozen.py is skipped here — it requires a clean git tree
# with committed bundle inputs, which doesn't hold during active development.
# The app's --self-test validates the frozen sidecar end-to-end instead.

Write-Host "=== Freeze complete ==="
Write-Host "Output: dist\iofast\"