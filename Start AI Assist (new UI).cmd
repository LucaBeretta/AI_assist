@echo off
setlocal
rem Reuse the same interpreter discovery, private setup and detached launcher.
set "AI_ASSIST_WEB_UI=1"
call "%~dp0Start AI Assist.cmd"
exit /b %ERRORLEVEL%
