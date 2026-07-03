@echo off
rem Function: Launch the memdir Python hook dispatcher on Windows.
rem Purpose: Avoid shell fallback operators in Codex hook manifests.
setlocal

set "SCRIPT_DIR=%~dp0"

call :run py -3 "%SCRIPT_DIR%memdir_hook.py" %*
set "HOOK_STATUS=%ERRORLEVEL%"
if "%HOOK_STATUS%"=="0" exit /b 0
if /I "%~1"=="stop" if not "%HOOK_STATUS%"=="127" exit /b %HOOK_STATUS%

call :run python "%SCRIPT_DIR%memdir_hook.py" %*
set "HOOK_STATUS=%ERRORLEVEL%"
if "%HOOK_STATUS%"=="0" exit /b 0
if /I "%~1"=="stop" if not "%HOOK_STATUS%"=="127" exit /b %HOOK_STATUS%

call :run python3 "%SCRIPT_DIR%memdir_hook.py" %*
set "HOOK_STATUS=%ERRORLEVEL%"
if "%HOOK_STATUS%"=="0" exit /b 0
if /I "%~1"=="stop" if not "%HOOK_STATUS%"=="127" exit /b %HOOK_STATUS%

echo [memdir_hook] skipped: Python 3.11+ launcher failed; install Python or enable py/python on PATH. 1>&2
if /I not "%~1"=="stop" echo {"continue":true,"suppressOutput":true}
if /I "%~1"=="stop" exit /b 1
exit /b 0

:run
where "%~1" >nul 2>nul
if errorlevel 1 exit /b 127
%*
exit /b %ERRORLEVEL%
