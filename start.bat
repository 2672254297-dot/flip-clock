@echo off
rem ---------------------------------------------------------------
rem  Flip Clock Overlay - start host (console stays open with logs)
rem ---------------------------------------------------------------
cd /d "%~dp0"
title Flip Clock Overlay

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH. Install Python 3 or edit this file.
  pause >nul
  exit /b 1
)

python "%~dp0app\flipclock_overlay.py" %*
echo.
echo Host exited. Press any key to close this window.
pause >nul
