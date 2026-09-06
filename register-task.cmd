@echo off
setlocal EnableExtensions
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"

set "TASK_NAME=cloud-agent-sync"
set "RUNNER=%DIR%\run-sync-task.cmd"
set "TIME=23:00"

if /I "%~1"=="/unregister" goto :unregister
if /I "%~1"=="-Unregister" goto :unregister
if /I "%~1"=="/u" goto :unregister
if not "%~1"=="" set "TIME=%~1"

if not exist "%RUNNER%" (
  echo [ERROR] Missing: %RUNNER%
  exit /b 1
)

rem Weekly Sunday. /F overwrites an older daily task with the same name.
rem /IT runs only while this Windows user is logged on (GitHub auth).
schtasks /Create /TN "%TASK_NAME%" /TR "\"%RUNNER%\"" /SC WEEKLY /D SUN /ST %TIME% /IT /F
if errorlevel 1 (
  echo [ERROR] Failed to register scheduled task.
  exit /b 1
)

echo.
echo Registered scheduled task: %TASK_NAME%
echo Command: %RUNNER%
echo Weekly: Sunday %TIME%
echo This task only runs sync. It does not call AI.
echo New source folders under D:\dev are auto-added.
echo GitHub auth works best while you are logged on to Windows.
exit /b 0

:unregister
schtasks /Query /TN "%TASK_NAME%" >nul 2>&1
if errorlevel 1 (
  echo Task not found: %TASK_NAME%
  exit /b 0
)
schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 (
  echo [ERROR] Failed to remove scheduled task.
  exit /b 1
)
echo Removed scheduled task: %TASK_NAME%
exit /b 0
