@echo off
title Build Standalone Executable for Video Studio Backend
color 0D
echo ===================================================
echo   Building video_studio_backend.exe with PyInstaller
echo ===================================================
echo.

rem Build inside a private virtual environment so the exe always contains exactly
rem the hash-checked packages in requirements-dev.lock, whatever else is installed.
set "VENV=%~dp0.venv-build"
if not exist "%VENV%\Scripts\python.exe" (
    echo Creating build environment in .venv-build ...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Could not create the build environment. Is Python 3.10+ on PATH?
        pause
        exit /b 1
    )
)

"%VENV%\Scripts\python.exe" -m pip install --require-hashes -r "%~dp0requirements-dev.lock"
if errorlevel 1 (
    echo [ERROR] Failed to install the locked build dependencies.
    pause
    exit /b 1
)

echo.
echo Checking the code before building (compile, lint, unit tests)...
"%VENV%\Scripts\python.exe" "%~dp0scripts\check.py" --quick
if errorlevel 1 (
    echo [ERROR] Checks failed - no exe was built. Fix the failures above and retry.
    pause
    exit /b 1
)

echo.
echo Building from video_studio_backend.spec (folder build, .env not bundled)...
pushd "%~dp0"
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --clean "video_studio_backend.spec"
set BUILD_RC=%ERRORLEVEL%
popd

if %BUILD_RC% EQU 0 (
    echo.
    echo ===================================================
    echo [SUCCESS] Executable created at:
    echo %~dp0dist\video_studio_backend\video_studio_backend.exe
    echo.
    echo Copy the whole dist\video_studio_backend folder to move it.
    echo Optional: put a .env next to the exe to change paths or the port.
    echo ===================================================
) else (
    echo.
    echo [ERROR] Build failed. Check compiler output above.
)

pause
