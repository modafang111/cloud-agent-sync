@echo off
setlocal EnableExtensions
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"

where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%DIR%\install.ps1"
  if not errorlevel 1 exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%DIR%\install.ps1"
if not errorlevel 1 exit /b 0

echo.
echo install.ps1 failed. Trying Python installer...
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 "%DIR%\install.py"
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python "%DIR%\install.py"
  exit /b %ERRORLEVEL%
)

echo [ERROR] Could not run installer.
echo Install Python 3, or run install.ps1 in PowerShell 7.
exit /b 1
