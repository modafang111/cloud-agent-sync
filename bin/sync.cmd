@echo off
setlocal EnableExtensions
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 "%ROOT%\sync.py" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python "%ROOT%\sync.py" %*
  exit /b %ERRORLEVEL%
)

where python3 >nul 2>&1
if %ERRORLEVEL%==0 (
  python3 "%ROOT%\sync.py" %*
  exit /b %ERRORLEVEL%
)

echo [ERROR] Python 3 が見つかりません。
echo cloud-agent-sync の実行には Python 3 が必要です。
echo https://www.python.org/downloads/ からインストールし、
echo インストーラの "Add python.exe to PATH" を有効にしてください。
exit /b 1
