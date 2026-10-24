@echo off
title Launch Video Studio Application
color 0B
echo ===================================================
echo   Starting Video Cut, Join & Recovery Studio
echo ===================================================
echo.
echo 1. Launching the loopback-only backend on 127.0.0.1:8080...
echo    The browser opens automatically once the server is bound.
echo    Do NOT open video_studio_app.html directly - it needs the
echo    per-launch API token that the server injects into the page.
echo.
rem Use the locked .venv from setup_studio.bat when it exists, else the global Python.
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
start "Video Studio Backend" cmd /k ""%PY%" "%~dp0video_studio_backend.py" --open-browser"

echo [SUCCESS] Studio launch requested.
echo You can close this launcher window; keep the backend window open.
timeout /t 3 >nul
exit
