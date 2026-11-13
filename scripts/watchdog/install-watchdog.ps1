# Registers the "iofast-watchdog" scheduled task: every 15 min and at logon, start the iofast tray app
# if it is not running, so phone captures waiting in the server inbox get filed.
# conhost --headless keeps it windowless. A .vbs in AppData was blocked on Eric's PC ("Can not find script file").
$cmd = "if (-not (Get-Process iofast -ErrorAction SilentlyContinue)) { Start-Process -FilePath (Join-Path `$env:LOCALAPPDATA 'iofast\iofast.exe') }"
$a = New-ScheduledTaskAction -Execute "conhost.exe" -Argument "--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command `"$cmd`""
$t1 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
$t2 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
$p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName iofast-watchdog -Action $a -Trigger $t1,$t2 -Settings $s -Principal $p -Force `
  -Description "Restarts the iofast tray app if closed so phone captures get filed" | Out-Null
"iofast-watchdog registered"
