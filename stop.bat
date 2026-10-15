@echo off
rem ---------------------------------------------------------------
rem  Flip Clock Overlay - stop host and kill the clock browser
rem ---------------------------------------------------------------
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH. Install Python 3 or edit this file.
  pause >nul
  exit /b 1
)
python "%~dp0app\flipclock_overlay.py" --stop
echo.
echo Stopped. Press any key to close.
pause >nul
