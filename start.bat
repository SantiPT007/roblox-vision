@echo off
:: Request administrator privileges via UAC
:: (required for global hotkeys via the keyboard library)
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: Now running as admin
cd /d "%~dp0"

:: Pin to the exact Python the current user normally uses (same logic as install.bat)
for /f "delims=" %%P in ('powershell -NoProfile -Command "[System.Environment]::GetEnvironmentVariable(\"PATH\",\"User\").Split(\";\") | Where-Object { $_ -like \"*Python*\" } | Select-Object -First 1"') do set "PYDIR=%%P"

set "PYTHON=python"
if defined PYDIR (
    if exist "%PYDIR%\python.exe" set "PYTHON=%PYDIR%\python.exe"
)

:: Optional: pass --no-overlay to run headless
:: "%PYTHON%" main.py --no-overlay

"%PYTHON%" main.py %*

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Character Tracker exited with code %errorlevel%.
    pause
)
