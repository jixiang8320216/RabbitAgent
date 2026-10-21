@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"

echo RabbitAgent installer
echo Project directory: %ROOT%

where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo Error: Python not found.
        exit /b 1
    )
    set "PYTHON=py -3"
) else (
    set "PYTHON=python"
)

%PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)"
if errorlevel 1 (
    echo Error: Python >= 3.12 is required.
    %PYTHON% --version
    exit /b 1
)

%PYTHON% --version

where uv >nul 2>nul
if errorlevel 1 (
    set "USE_UV=0"
    echo uv not detected, falling back to pip.
) else (
    set "USE_UV=1"
    echo uv detected, using fast install path.
)

if exist "%VENV%" (
    choice /M ".venv already exists, rebuild it"
    if errorlevel 2 (
        echo Keeping existing virtual environment.
    ) else (
        rmdir /s /q "%VENV%"
    )
)

if not exist "%VENV%" (
    if "!USE_UV!"=="1" (
        uv venv "%VENV%" --python %PYTHON%
    ) else (
        %PYTHON% -m venv "%VENV%"
    )

    if errorlevel 1 (
        echo Failed to create virtual environment.
        exit /b 1
    )
)

call "%VENV%\Scripts\activate.bat"

if "!USE_UV!"=="1" (
    uv pip install --upgrade pip --python "%VENV%\Scripts\python.exe"
    if exist "%ROOT%pyproject.toml" (
        uv pip install --python "%VENV%\Scripts\python.exe" -e "%ROOT%"
    ) else (
        echo No pyproject.toml found, skipping editable install. Run with: python main.py
    )
    if exist "%ROOT%requirements.txt" (
        uv pip install --python "%VENV%\Scripts\python.exe" -r "%ROOT%requirements.txt"
    )
) else (
    python -m pip install --upgrade pip
    if exist "%ROOT%pyproject.toml" (
        python -m pip install -e "%ROOT%"
    ) else (
        echo No pyproject.toml found, skipping editable install. Run with: python main.py
    )
    if exist "%ROOT%requirements.txt" (
        python -m pip install -r "%ROOT%requirements.txt"
    )
)

if errorlevel 1 (
    echo Dependency installation failed.
    exit /b 1
)

set "PROJ=%ROOT%"
if "%PROJ:~-1%"=="\" set "PROJ=%PROJ:~0,-1%"

set "BIN=%USERPROFILE%\bin"
if not exist "%BIN%" mkdir "%BIN%"
set "LAUNCHER=%BIN%\rabbit-clishell.cmd"

(
    echo @echo off
    echo chcp 65001 ^>nul
    echo cd /d "%PROJ%"
    echo "%PROJ%\.venv\Scripts\python.exe" main.py %%*
) > "%LAUNCHER%"

if errorlevel 1 (
    echo Failed to register launcher.
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$bin=$env:USERPROFILE+'\bin'; $old=[Environment]::GetEnvironmentVariable('Path','User'); $items=@($old -split ';' | Where-Object { $_ }); if (-not ($items -contains $bin)) { [Environment]::SetEnvironmentVariable('Path', (($items + $bin) -join ';'), 'User') }"

if errorlevel 1 (
    echo Failed to update user PATH.
    exit /b 1
)

echo RabbitAgent setup complete.
echo Please close and reopen the terminal.
echo You can run rabbit-clishell from any directory.

endlocal
