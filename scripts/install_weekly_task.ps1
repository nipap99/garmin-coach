# Registers a Windows Scheduled Task that generates the weekly running schedule
# PDF every Sunday at 10:00. Re-run this any time to update the schedule; it
# replaces the existing task. No admin rights needed (runs as the current user).

$ErrorActionPreference = "Stop"

$TaskName = "GarminCoach Weekly Schedule"
$Root     = "C:\Users\papad\Desktop\garmin-coach"
$PythonW  = Join-Path $Root ".venv\Scripts\pythonw.exe"   # windowless — no console flash
$Script   = Join-Path $Root "weekly_schedule.py"

$action = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Script`"" -WorkingDirectory $Root

# Every Sunday at 10:00 local time.
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 10:00AM

# Reliability: run a missed occurrence when the machine wakes / next logon,
# and allow waking the machine to run it.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

# Run as the current user, only when logged on (needs the local DB + Downloads).
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Reads the local garmin-coach DB and writes 'running schedule - week of <date>.pdf' to Downloads." `
    -Force | Out-Null

Write-Host "Registered scheduled task: '$TaskName' (Sundays 10:00)."
Write-Host "Next run:" (Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime
