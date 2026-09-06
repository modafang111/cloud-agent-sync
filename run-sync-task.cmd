@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0bin\sync.cmd"
set "ERR=%ERRORLEVEL%"
if %ERR%==0 exit /b 0

rem sync.py already emails via notify.py when Python ran.
rem This path is only for "Python missing from PATH".
where py >nul 2>&1
if %ERRORLEVEL%==0 exit /b %ERR%
where python >nul 2>&1
if %ERRORLEVEL%==0 exit /b %ERR%
where python3 >nul 2>&1
if %ERRORLEVEL%==0 exit /b %ERR%

call "%~dp0notify.cmd" --event python_missing --note "Python 3 was not found on PATH. Weekly sync did not run."
exit /b %ERR%
