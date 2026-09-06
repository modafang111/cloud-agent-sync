@echo off
setlocal EnableExtensions
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 "%DIR%\notify.py" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python "%DIR%\notify.py" %*
  exit /b %ERRORLEVEL%
)
where python3 >nul 2>&1
if %ERRORLEVEL%==0 (
  python3 "%DIR%\notify.py" %*
  exit /b %ERRORLEVEL%
)
if exist "%SystemRoot%\py.exe" (
  "%SystemRoot%\py.exe" -3 "%DIR%\notify.py" %*
  exit /b %ERRORLEVEL%
)
echo [ERROR] Python 3 が見つかりません。通知メールを送れません。
exit /b 1
