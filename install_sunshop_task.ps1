# Registers the "SunShop" scheduled task: runs the server at boot as SYSTEM, so it keeps
# running when nobody is signed in. Run from an elevated (Administrator) PowerShell.
#   Stop:     Stop-ScheduledTask SunShop      Start: Start-ScheduledTask SunShop
#   Remove:   Unregister-ScheduledTask SunShop -Confirm:$false
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$bat = Join-Path $dir 'run_server_task.bat'

$action    = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$bat`"" -WorkingDirectory $dir
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName 'SunShop' -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'SunShop purchasing server (waitress on port 8080)' -Force | Out-Null
Write-Host 'Registered scheduled task "SunShop".'

# Stop a server started by hand (start_sunshop.bat) so the task can take over port 8080.
Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Write-Host "Stopping existing server (PID $_)"; Stop-Process -Id $_ -Force }
Start-Sleep -Seconds 2

Start-ScheduledTask -TaskName 'SunShop'
Write-Host 'Started. Log: ' (Join-Path $dir 'logs\server.log')
