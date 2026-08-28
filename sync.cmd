@echo off
setlocal EnableExtensions
call "%~dp0bin\sync.cmd" %*
exit /b %ERRORLEVEL%
