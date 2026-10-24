@echo off
title Video Studio Setup
echo ===================================================
echo   Video Studio setup: WSL2, recovery tools, media tools, Python packages
echo   Safe to re-run. Add -CheckOnly to only report without changing anything.
echo ===================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_studio.ps1" %*
set SETUP_RC=%ERRORLEVEL%
echo.
pause
exit /b %SETUP_RC%
