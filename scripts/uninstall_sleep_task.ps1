# Removes the daily sleep sync from the per-user startup registry key.
# Run:  powershell -ExecutionPolicy Bypass -File scripts\uninstall_sleep_task.ps1

$ErrorActionPreference = "Stop"

$name   = "GarminCoachSleepSync"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"

if (Get-ItemProperty -Path $runKey -Name $name -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $runKey -Name $name
    Write-Host "Removed '$name' from logon startup."
} else {
    Write-Host "'$name' was not registered — nothing to remove."
}
