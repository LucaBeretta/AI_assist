@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 "%~dp0AIassist.py"
if errorlevel 1 goto failed
exit /b 0

:try_python
where python >nul 2>&1
if errorlevel 1 goto missing_python
python "%~dp0AIassist.py"
if errorlevel 1 goto failed
exit /b 0

:missing_python
echo Python was not found. Install Python with Tkinter, then try again.
echo https://www.python.org/downloads/
goto finish

:failed
echo.
echo AI Assist could not start. The error is shown above.
echo Check that Python includes Tkinter, then try again.

:finish
echo.
pause
exit /b 1
