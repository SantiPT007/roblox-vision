@echo off
:: Request administrator privileges via UAC
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: Now running as admin
cd /d "%~dp0"
echo ============================================
echo  Character Tracker - Dependency Installer
echo ============================================
echo.

:: Pin to the exact Python that the current user normally uses.
:: 'where python' inside an elevated shell can resolve to a different install,
:: so we ask the non-elevated user profile's PATH via PowerShell instead.
for /f "delims=" %%P in ('powershell -NoProfile -Command "[System.Environment]::GetEnvironmentVariable(\"PATH\",\"User\").Split(\";\") | Where-Object { $_ -like \"*Python*\" } | Select-Object -First 1"') do set "PYDIR=%%P"

:: Fall back to whatever python is on the system PATH if no user-level dir found
set "PYTHON=python"
if defined PYDIR (
    if exist "%PYDIR%\python.exe" set "PYTHON=%PYDIR%\python.exe"
)

echo Using Python: %PYTHON%
"%PYTHON%" --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python 3.11+ and add it to PATH.
    pause
    exit /b 1
)
"%PYTHON%" --version

echo.
echo [1/4] Upgrading pip and pinning setuptools...
"%PYTHON%" -m pip install --upgrade pip --no-cache-dir
if %errorlevel% neq 0 ( echo [WARN] pip upgrade failed, continuing anyway. )
:: setuptools 81+ removes pkg_resources which boxmot requires at import time
"%PYTHON%" -m pip install "setuptools<81" --no-cache-dir --prefer-binary
if %errorlevel% neq 0 ( echo [WARN] setuptools pin failed, continuing anyway. )

echo.
echo [2/4] Installing ONNX runtime ^(DirectML — GPU-accelerated on any DirectX 12 GPU^)...
"%PYTHON%" -m pip install onnxruntime-directml --no-cache-dir --prefer-binary
if %errorlevel% neq 0 (
    echo [ERROR] onnxruntime-directml install failed.
    pause
    exit /b 1
)

echo.
echo [3/4] Installing OpenCV, PyQt6, pywin32, pyyaml, keyboard, numpy...
for %%P in (opencv-python PyQt6 pywin32 pyyaml keyboard numpy) do (
    echo   Installing %%P...
    "%PYTHON%" -m pip install %%P --no-cache-dir --prefer-binary
    if errorlevel 1 echo [WARN] %%P failed - check manually after.
)

echo.
echo [4/4] Installing remaining packages ^(boxmot, dxcam, psutil, etc.^)...
for %%P in (dxcam psutil nvidia-ml-py pyserial interception-python) do (
    echo   Installing %%P...
    "%PYTHON%" -m pip install %%P --no-cache-dir --prefer-binary
    if errorlevel 1 echo [WARN] %%P failed - check manually after.
)
:: boxmot must be installed with --no-deps because its pinned numpy/pandas/regex
:: versions have no Python 3.14 wheels and fail to build from source.
echo   Installing boxmot (no-deps to avoid source builds on Python 3.14)...
"%PYTHON%" -m pip install boxmot --no-cache-dir --prefer-binary --no-deps
if errorlevel 1 echo [WARN] boxmot failed - check manually after.
:: Install remaining boxmot deps that weren't covered above
for %%P in (filterpy ftfy gdown gitpython lapx loguru pandas regex scikit-learn yacs) do (
    "%PYTHON%" -m pip install %%P --no-cache-dir --prefer-binary
    if errorlevel 1 echo [WARN] %%P failed - check manually after.
)

echo.
echo ============================================
echo  Installation complete.
echo  Place your .onnx model in models/, edit
echo  config.yaml, then run start.bat to launch.
echo.
echo  If any [WARN] appeared above, re-run this
echo  script or install that package manually.
echo ============================================
pause
