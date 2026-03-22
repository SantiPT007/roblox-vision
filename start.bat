@echo off
cd /d "%~dp0"

:: Restore the user-level PATH so Python and its DLLs are discoverable.
:: We do NOT request admin — SendInput and keyboard hooks work without elevation,
:: and UAC elevation strips user PATH which breaks onnxruntime DLL loading.
for /f "delims=" %%U in ('powershell -NoProfile -Command "[System.Environment]::GetEnvironmentVariable(\"PATH\",\"User\")"') do set "USER_PATH=%%U"
set "PATH=%PATH%;%USER_PATH%"

:: Find the user's Python (same logic as install.bat)
for /f "delims=" %%P in ('powershell -NoProfile -Command "[System.Environment]::GetEnvironmentVariable(\"PATH\",\"User\").Split(\";\") | Where-Object { $_ -like \"*Python*\" } | Select-Object -First 1"') do set "PYDIR=%%P"

set "PYTHON=python"
if defined PYDIR (
    if exist "%PYDIR%\python.exe" set "PYTHON=%PYDIR%\python.exe"
)

"%PYTHON%" main.py %*

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Character Tracker exited with code %errorlevel%.
    pause
)
