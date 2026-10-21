@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "DEBUG_MODE=%DEBUG_MODE%"
if "%DEBUG_MODE%"=="" set "DEBUG_MODE=0"

if "%DEBUG_MODE%"=="1" echo [DEBUG] args: %*

rem Resolve script directory and project directory
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%"
set "VENV=%PROJECT_DIR%.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "ACTIVATE=%VENV%\Scripts\activate.bat"

rem Locate the virtual environment
if not exist "%PYTHON%" (
    if exist "%PROJECT_DIR%..\.venv\Scripts\python.exe" (
        set "PROJECT_DIR=%PROJECT_DIR%.."
        set "VENV=!PROJECT_DIR!.venv"
        set "PYTHON=!VENV!\Scripts\python.exe"
        set "ACTIVATE=!VENV!\Scripts\activate.bat"
    )
)

if not exist "%PYTHON%" (
    echo Error: virtual environment not found: %VENV% 1>&2
    echo Run rabbit-setup.bat first. 1>&2
    exit /b 1
)

if not exist "%ACTIVATE%" (
    echo Error: incomplete virtual environment: %VENV% 1>&2
    exit /b 1
)

rem Activate the virtual environment
call "%ACTIVATE%"
if errorlevel 1 exit /b 1

rem Set project environment variable
set "RABBIT_PROJECT_DIR=%PROJECT_DIR%"

rem Rebuild PYTHONPATH
set "PYTHONPATH=%PROJECT_DIR%"

rem Force UTF-8
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if "%DEBUG_MODE%"=="1" (
    echo [DEBUG] PROJECT_DIR=%PROJECT_DIR%
    echo [DEBUG] VENV=%VENV%
    echo [DEBUG] PYTHONPATH=%PYTHONPATH%
)

rem Verify entry file exists
if not exist "%PROJECT_DIR%main.py" (
    echo Error: entry file main.py not found. 1>&2
    exit /b 1
)

echo Starting RabbitAgent CLI... 1>&2

rem Pass exit code through
"%PYTHON%" "%PROJECT_DIR%main.py" %*
set "EXIT_CODE=%ERRORLEVEL%"

endlocal & exit /b %EXIT_CODE%
