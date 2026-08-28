@echo off
setlocal EnableExtensions
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%DIR%\register-task.ps1" %*
exit /b %ERRORLEVEL%
