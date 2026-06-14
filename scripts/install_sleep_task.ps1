# Registers the daily sleep sync to run at logon — via the per-user startup
# registry key (HKCU\...\Run). This needs NO admin rights (unlike Task
# Scheduler, which is locked down on this machine).
#
# How it behaves: the launcher runs at every logon, but daily_sleep_sync.py
# guards to actually sync at most once per calendar day. So the FIRST logon of
# the day opens a browser briefly and grabs last night's sleep; later logons
# that day just start Python, see "already synced", and exit (no browser).
#
# Run once:  powershell -ExecutionPolicy Bypass -File scripts\install_sleep_task.ps1
# Remove:    powershell -ExecutionPolicy Bypass -File scripts\uninstall_sleep_task.ps1

$ErrorActionPreference = "Stop"

$root   = Split-Path -Parent $PSScriptRoot
$bat    = Join-Path $root "scripts\run_sleep_sync.bat"
$name   = "GarminCoachSleepSync"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (-not (Test-Path $bat)) { throw "Launcher not found: $bat" }

Set-ItemProperty -Path $runKey -Name $name -Value "`"$bat`""

Write-Host "Installed '$name' to run at logon (no admin needed)."
Write-Host "First logon each day: a browser briefly opens to sync last night's sleep."
Write-Host "Logs: $root\data\sleep_sync.log"
Write-Host "To remove: powershell -ExecutionPolicy Bypass -File scripts\uninstall_sleep_task.ps1"
