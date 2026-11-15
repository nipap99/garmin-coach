@echo off
title Garmin Coach
cd /d "%~dp0"

echo.
echo  ==========================================
echo   Garmin Coach - Starting up...
echo  ==========================================
echo.
echo  - Server will be at http://localhost:8000
echo  - Your browser will open automatically
echo  - Press Ctrl+C here to stop the app
echo.

:: Open the browser only once the server is actually listening (polls port 8000),
:: so it never races ahead of startup. Runs hidden - no extra console window.
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\open_when_ready.ps1"

:: Start the server (keeps this window alive while app is running)
.venv\Scripts\python.exe -m uvicorn backend.main:app --reload

echo.
echo  App stopped. Press any key to close.
pause >nul
