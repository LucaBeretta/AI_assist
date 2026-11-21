@echo off
setlocal
cd /d "%~dp0"
rem Use an installed Python with the web UI dependencies; if none works,
rem offer to download a private, checksum-verified Python into .\runtime.

set "APP_DIR=%~dp0"
set "PRIVATE_PYTHON=%APP_DIR%runtime\python\python.exe"
set "RESULT="

if defined AI_ASSIST_PYTHON call :try "%AI_ASSIST_PYTHON%" && goto finished
rem A previously installed private runtime is known to work, so prefer it.
if exist "%PRIVATE_PYTHON%" call :try "%PRIVATE_PYTHON%" && goto finished
where py >nul 2>&1 && call :try py -3 && goto finished
where python >nul 2>&1 && call :try python && goto finished

echo No installed Python with pywebview was found.
powershell -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%install_runtime.ps1"
if errorlevel 1 goto unavailable
call :try "%PRIVATE_PYTHON%" && goto finished

:unavailable
echo.
echo AI Assist could not find or set up a compatible Python.
echo Run this launcher again to retry the private setup, or install Python
echo from https://www.python.org/downloads/ and install requirements.txt.
goto pause_and_fail

:finished
if "%RESULT%"=="ok" exit /b 0
echo.
echo AI Assist could not start. The error is shown above.

:pause_and_fail
echo.
pause
exit /b 1

rem Probe a candidate (all arguments form the command). Returns 0 after the
rem app has run, setting RESULT to ok or failed; returns 1 if unusable.
:try
echo Checking Python runtime: %*
%* "%APP_DIR%gui_runtime.py" --probe
if errorlevel 1 goto try_unusable
%* "%APP_DIR%gui_runtime.py" --ask-shortcut
rem Start the app detached with pythonw so closing this window does not close
rem it (errors go to %%LOCALAPPDATA%%\AI Assist\last-run.log). Without pythonw,
rem run it here as before.
%* "%APP_DIR%gui_runtime.py" --launch "%APP_DIR%AIassist.py"
if not errorlevel 1 (set "RESULT=ok" & exit /b 0)
%* "%APP_DIR%AIassist.py"
if errorlevel 1 (set "RESULT=failed") else (set "RESULT=ok")
exit /b 0

:try_unusable
echo.
exit /b 1
