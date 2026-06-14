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

:: Open browser after 3 seconds in app mode (single clean window, no extra tabs)
start "" cmd /c "timeout /t 3 /nobreak >nul && (start msedge --app=http://localhost:8000 2>nul || start chrome --app=http://localhost:8000 2>nul || start http://localhost:8000)"

:: Start the server (keeps this window alive while app is running)
.venv\Scripts\python.exe -m uvicorn backend.main:app --reload

echo.
echo  App stopped. Press any key to close.
pause >nul
