@echo off
rem Function: Launch the memdir CLI on Windows.
rem Purpose: Try common Python launchers for manual project-memdir commands.
setlocal

set "SCRIPT_DIR=%~dp0"

call :run py -3 "%SCRIPT_DIR%memdir_cli.py" %*
set "CLI_STATUS=%ERRORLEVEL%"
if "%CLI_STATUS%"=="0" exit /b 0
if not "%CLI_STATUS%"=="127" exit /b %CLI_STATUS%

call :run python "%SCRIPT_DIR%memdir_cli.py" %*
set "CLI_STATUS=%ERRORLEVEL%"
if "%CLI_STATUS%"=="0" exit /b 0
if not "%CLI_STATUS%"=="127" exit /b %CLI_STATUS%

call :run python3 "%SCRIPT_DIR%memdir_cli.py" %*
set "CLI_STATUS=%ERRORLEVEL%"
if "%CLI_STATUS%"=="0" exit /b 0
if not "%CLI_STATUS%"=="127" exit /b %CLI_STATUS%

echo [memdir_cli] failed: Python 3.11+ launcher not found; install Python or enable py/python on PATH. 1>&2
exit /b 1

:run
where "%~1" >nul 2>nul
if errorlevel 1 exit /b 127
set "PYTHONUTF8=1"
%*
exit /b %ERRORLEVEL%
