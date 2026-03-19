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
echo ============================================
echo  IMPORTANT: Close your IDE and other heavy
echo  applications before continuing.
echo  PyTorch is ~2 GB and will spike RAM/disk.
echo ============================================
echo.
pause

echo [1/5] Upgrading pip and pinning setuptools...
"%PYTHON%" -m pip install --upgrade pip --no-cache-dir
if %errorlevel% neq 0 ( echo [WARN] pip upgrade failed, continuing anyway. )
:: setuptools 81+ removes pkg_resources which boxmot requires at import time
"%PYTHON%" -m pip install "setuptools<81" --no-cache-dir --prefer-binary
if %errorlevel% neq 0 ( echo [WARN] setuptools pin failed, continuing anyway. )

echo.
echo ============================================
echo  Select your GPU vendor:
echo    1 = NVIDIA  (installs PyTorch CUDA 12.8)
echo    2 = AMD     (installs PyTorch CPU + torch-directml)
echo    3 = CPU only
echo ============================================
set /p GPU_CHOICE="Enter 1, 2 or 3: "

echo.
if "%GPU_CHOICE%"=="2" (
    echo [2/5] Installing PyTorch ^(CPU build for DirectML^) - no cache, binary only...
    echo       AMD users: torch-directml handles GPU compute via DirectX 12.
    "%PYTHON%" -m pip install torch torchvision ^
        --index-url https://download.pytorch.org/whl/cpu ^
        --no-cache-dir ^
        --prefer-binary ^
        --no-deps
) else if "%GPU_CHOICE%"=="3" (
    echo [2/5] Installing PyTorch ^(CPU only^)...
    "%PYTHON%" -m pip install torch torchvision ^
        --index-url https://download.pytorch.org/whl/cpu ^
        --no-cache-dir ^
        --prefer-binary ^
        --no-deps
) else (
    echo [2/5] Installing PyTorch ^(CUDA 12.8^) - no cache, no compile, binary only...
    echo       This is the large download ^(~2 GB^). Installing alone to avoid RAM spike.
    "%PYTHON%" -m pip install torch torchvision ^
        --index-url https://download.pytorch.org/whl/cu128 ^
        --no-cache-dir ^
        --prefer-binary ^
        --no-deps
)
if %errorlevel% neq 0 (
    echo [ERROR] PyTorch install failed.
    pause
    exit /b 1
)

echo.
echo [3/5] Installing torchvision dependencies (numpy etc)...
"%PYTHON%" -m pip install numpy --no-cache-dir --prefer-binary
if %errorlevel% neq 0 ( echo [WARN] numpy install failed. )

echo.
echo [4/5] Installing OpenCV, PyQt6, pywin32, pyyaml, keyboard...
echo       (all pure wheels, no compilation)
for %%P in (opencv-python PyQt6 pywin32 pyyaml keyboard) do (
    echo   Installing %%P...
    "%PYTHON%" -m pip install %%P --no-cache-dir --prefer-binary
    if errorlevel 1 echo [WARN] %%P failed - check manually after.
)

echo.
echo [5/5] Installing ML packages (ultralytics, boxmot, dxcam)...
echo       Using --prefer-binary to avoid compiler invocation.
for %%P in (ultralytics dxcam) do (
    echo   Installing %%P...
    "%PYTHON%" -m pip install %%P --no-cache-dir --prefer-binary
    if errorlevel 1 echo [WARN] %%P failed - check manually after.
)
:: boxmot must be installed with --no-deps because its pinned numpy/pandas/regex
:: versions have no Python 3.14 wheels and fail to build from source.
:: Dependencies are already installed by the steps above.
echo   Installing boxmot (no-deps to avoid source builds on Python 3.14)...
"%PYTHON%" -m pip install boxmot --no-cache-dir --prefer-binary --no-deps
if errorlevel 1 echo [WARN] boxmot failed - check manually after.
:: Install remaining boxmot deps that weren't covered above
for %%P in (filterpy ftfy gdown gitpython lapx loguru pandas regex scikit-learn yacs) do (
    "%PYTHON%" -m pip install %%P --no-cache-dir --prefer-binary
    if errorlevel 1 echo [WARN] %%P failed - check manually after.
)

if "%GPU_CHOICE%"=="2" (
    echo.
    echo [AMD] Installing torch-directml for AMD/Intel GPU acceleration...
    "%PYTHON%" -m pip install torch-directml --no-cache-dir --prefer-binary
    if errorlevel 1 echo [WARN] torch-directml failed - AMD GPU acceleration will not be available.
)

echo.
echo ============================================
echo  Installation complete.
echo  Edit config.yaml to set your target window,
echo  then run start.bat to launch.
echo.
echo  If any [WARN] appeared above, re-run this
echo  script or install that package manually.
echo ============================================
pause
