@echo off
rem ---------------------------------------------------------------
rem  Flip Clock - MANUAL MODE: show the clock fullscreen right now
rem  No idle watching, no auto jump-out, no auto exit.
rem  Mouse pointer auto-hides (move the mouse to show it for 3s).
rem  Press Esc to exit.   (config.json is not modified)
rem ---------------------------------------------------------------
cd /d "%~dp0"
title Flip Clock (manual)
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH. Install Python 3 or edit this file.
  pause >nul
  exit /b 1
)
echo Showing the flip clock fullscreen now (manual mode).
echo   Esc / Alt+F4 = exit      gear icon (top-right) = settings
echo.
python "%~dp0app\flipclock_overlay.py" --manual %*
echo.
echo Clock closed. Press any key to close this window.
pause >nul
