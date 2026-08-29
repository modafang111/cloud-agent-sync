@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0bin\sync.cmd"
set "ERR=%ERRORLEVEL%"
if %ERR%==0 exit /b 0

rem sync.py already emails on conflict/error. This is only for "Python missing".
where py >nul 2>&1
if %ERRORLEVEL%==0 exit /b %ERR%
where python >nul 2>&1
if %ERRORLEVEL%==0 exit /b %ERR%
where python3 >nul 2>&1
if %ERRORLEVEL%==0 exit /b %ERR%

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0send-notify-fallback.ps1" -Reason "Python 3 was not found. Weekly sync did not run."
exit /b %ERR%
