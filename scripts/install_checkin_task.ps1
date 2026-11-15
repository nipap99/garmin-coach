# Registers a Windows Scheduled Task that runs the midweek check-in every
# Wednesday at 07:00 and saves its answer to
# "Desktop\Fitness Report\check-ins\check-in <date>.md".
#
# Since 2026-11-15 it runs on the Claude SUBSCRIPTION via
# agents\run_checkin_subscription.ps1 (Claude Code headless + the run-analyst
# subagent), so it costs nothing extra. The paid-API version
# (agents\checkin_agent.py) is kept below, commented out.
#
# Wednesday morning sits between the Tuesday VO2 session and the Thursday
# threshold session, so the question is whether to run Thursday as planned.
# The daily sync (23:58) has already pulled Tuesday's run by then.
# If the laptop is off at 07:00, StartWhenAvailable runs it at the next chance.
# No admin rights needed.

$ErrorActionPreference = "Stop"

$TaskName = "GarminCoach Midweek Check-in"
$Root     = "C:\Users\papad\Desktop\garmin-coach"
$Runner   = Join-Path $Root "agents\run_checkin_subscription.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Runner`"" -WorkingDirectory $Root

# Paid-API version (~$0.10-0.20 per run), kept for reference:
# $PythonW  = Join-Path $Root ".venv\Scripts\pythonw.exe"
# $Script   = Join-Path $Root "agents\checkin_agent.py"
# $Question = "Midweek check-in: how did Monday and Tuesday go, how is my recovery, and should I run Thursday's threshold session as planned, adjust it, or swap it for an easy run?"
# $action = New-ScheduledTaskAction -Execute $PythonW `
#     -Argument "`"$Script`" --save `"$Question`"" -WorkingDirectory $Root

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Wednesday -At 7:00AM

# Catch up a missed run, don't block on battery, never run two copies at once.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

# Run as the current user, only when logged on (needs the local DB + network).
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Runs the run-analyst subagent (Claude Code headless, on the subscription) over the local Garmin DB every Wednesday 07:00 and saves the answer to Desktop\Fitness Report\check-ins." `
    -Force | Out-Null

Write-Host "Registered scheduled task: '$TaskName'."
Write-Host "Trigger: Wednesdays 07:00 (catches up if missed)."
Write-Host "Next scheduled run:" (Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime
