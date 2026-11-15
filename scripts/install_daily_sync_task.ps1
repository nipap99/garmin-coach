# Registers a Windows Scheduled Task that refreshes Garmin data (sleep, runs,
# rides, calories) into the local garmin-coach DB.
#
# A laptop is usually asleep at 23:58, so the task runs on TWO triggers:
#   - daily at 23:58  (runs if the machine happens to be awake)
#   - at logon        (covers a fresh start each morning)
# daily_sync.py has a 3-hour cooldown so the two triggers can't double-sync, and
# the Garmin client backs off for an hour after any login 429, so this can never
# storm Garmin's login rate limit again. (An earlier 'on resume from sleep'
# trigger was removed — it fired on every wake and amplified login attempts.)
# No admin rights needed (runs as the current user, windowless via pythonw).

$ErrorActionPreference = "Stop"

$TaskName = "GarminCoach Daily Sync"
$Root     = "C:\Users\papad\Desktop\garmin-coach"
$PythonW  = Join-Path $Root ".venv\Scripts\pythonw.exe"   # windowless — no console flash
$Script   = Join-Path $Root "daily_sync.py"

$action = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Script`"" -WorkingDirectory $Root

# --- Triggers -------------------------------------------------------------
# 1) daily at 23:58
$tDaily = New-ScheduledTaskTrigger -Daily -At 11:58PM

# 2) at logon (current user), 2-minute settle delay
$tLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$tLogon.Delay = "PT2M"

$triggers = @($tDaily, $tLogon)

# Reliability: catch up a missed occurrence, allow wake, don't block on battery,
# and never start a second copy if one is already running.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

# Run as the current user, only when logged on (needs the local DB + network).
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal `
    -Description "Refreshes sleep, runs, rides and calories from Garmin into the local garmin-coach DB. Fires at 23:58, at logon, and on resume from sleep (reuses the cached token; no app needed)." `
    -Force | Out-Null

Write-Host "Registered scheduled task: '$TaskName'."
Write-Host "Triggers: daily 23:58, at logon (3 h cooldown between syncs)."
Write-Host "Next scheduled run:" (Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime
