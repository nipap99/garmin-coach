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

:: Open browser after 3 seconds (gives the server time to boot)
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8000"

:: Start the server (keeps this window alive while app is running)
.venv\Scripts\python.exe -m uvicorn backend.main:app --reload

echo.
echo  App stopped. Press any key to close.
pause >nul
