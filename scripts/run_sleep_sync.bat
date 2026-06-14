@echo off
REM Launcher for the daily sleep sync (called by Windows Task Scheduler).
cd /d "%~dp0\.."
.venv\Scripts\python.exe -m scripts.daily_sleep_sync
