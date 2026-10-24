@echo off
title Video Studio Backend Server
color 0A
echo ===================================================
echo   Video Cut, Join & Recovery Studio Backend Server
echo ===================================================
echo.
echo Bound to loopback only: http://127.0.0.1:8080/
echo A fresh API token is written to .runtime\api_token on every launch.
echo Keep this window open while using the Video Studio Web App.
echo Press Ctrl+C in this window to stop the server.
echo.

rem Use the locked .venv from setup_studio.bat when it exists, else the global Python.
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
"%PY%" "%~dp0video_studio_backend.py" --open-browser

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Failed to start Python backend server.
    echo Please make sure Python 3 is installed and added to your system PATH.
    pause
)
