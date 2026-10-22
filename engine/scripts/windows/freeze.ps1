# Freeze the engine sidecar for Windows.
# Usage: .\scripts\windows\freeze.ps1
#
# Steps:
#   1. Sync venv with ALL runtime + dev + build deps from pyproject.toml
#   2. Verify every runtime dependency imports (fail loud if not)
#   3. Run PyInstaller with packaging/iofast.spec
#   4. Verify the frozen bundle via packaging/verify_frozen.py

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..\..

Write-Host "=== Syncing venv with pyproject.toml deps ==="
& .venv\Scripts\pip.exe install -e ".[dev,build]" --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "=== Verifying runtime dependencies ==="
$verifyFile = Join-Path $env:TEMP "iofast-verify-deps.py"
@'
import sys
deps = ["scrapling", "trafilatura", "markdownify", "patchright",
        "cssselect", "lxml", "yt_dlp", "httpx"]
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

Write-Host "=== Verifying frozen bundle ==="
& .venv\Scripts\python.exe packaging\verify_frozen.py dist\iofast\iofast --dev
if ($LASTEXITCODE -ne 0) { throw "verify_frozen.py failed" }

Write-Host "=== Freeze complete ==="
Write-Host "Output: dist\iofast\"