@echo off
rem ---------------------------------------------------------------
rem  Flip Clock Overlay - open the settings page in your browser
rem  (run start.bat first; the page reads its config from the host)
rem  The URL (with this run's token) is printed by the host itself,
rem  so a non-default port is handled automatically.
rem ---------------------------------------------------------------
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH. Install Python 3 or edit this file.
  pause >nul
  exit /b 1
)
set "URL="
for /f "delims=" %%u in ('python "%~dp0app\flipclock_overlay.py" --print-url') do set "URL=%%u"
if "%URL%"=="" (
  echo [ERROR] Could not determine the settings page address.
  echo         Start the host first: start.bat
  pause >nul
  exit /b 1
)
echo Opening %URL%
echo   (if the page shows "not connected", start the host first: start.bat)
start "" "%URL%#settings"
