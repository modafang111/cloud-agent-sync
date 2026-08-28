@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0bin\sync.cmd"
exit /b %ERRORLEVEL%
