$ErrorActionPreference = "Continue"
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"

Write-Host "=== Stopping iofast ==="
Get-Process iofast -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Write-Host "=== Building NSIS installer ==="
Set-Location "C:\Users\USER\innovlabs\iofast-wt-win-shell\app"
pnpm tauri build --bundles nsis
if ($LASTEXITCODE -ne 0) { throw "NSIS build failed" }

Write-Host "=== Silent install ==="
$installer = "C:\Users\USER\innovlabs\iofast-wt-win-shell\app\src-tauri\target\release\bundle\nsis\iofast_0.1.0_x64-setup.exe"
& $installer /S
Start-Sleep -Seconds 3

Write-Host "=== Running self-test on installed build ==="
& "C:\Users\USER\AppData\Local\iofast\iofast.exe" --self-test
$selfTestExit = $LASTEXITCODE

Write-Host "=== Cargo test ==="
Set-Location "C:\Users\USER\innovlabs\iofast-wt-win-shell\app\src-tauri"
cargo test --message-format=short 2>&1 | Select-Object -Last 20

Write-Host "=== Rebase onto origin/main ==="
Set-Location "C:\Users\USER\innovlabs\iofast-wt-win-shell"
git fetch origin
git rebase origin/main

Write-Host "=== Engine pytest ==="
Set-Location "C:\Users\USER\innovlabs\iofast-wt-win-shell\engine"
$env:PYTHONUTF8 = "1"
& ".venv\Scripts\python.exe" -m pip install pytest-asyncio -q 2>$null
& ".venv\Scripts\python.exe" -m pytest -q -p no:cacheprovider 2>&1 | Select-Object -Last 10

Write-Host "=== Push ==="
Set-Location "C:\Users\USER\innovlabs\iofast-wt-win-shell"
git push origin win/shell --force-with-lease

Write-Host "=== Self-test exit code: $selfTestExit ==="
exit $selfTestExit