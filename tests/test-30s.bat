@echo off
rem ---------------------------------------------------------------
rem  Flip Clock Overlay - TEST RUN: show the clock after 30s idle
rem  (threshold only for this run; config.json is NOT modified)
rem  Lives in tests\ ; the host lives in ..\app\
rem ---------------------------------------------------------------
cd /d "%~dp0"
title Flip Clock Overlay (30s test)
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH. Install Python 3 or edit this file.
  pause >nul
  exit /b 1
)
echo Threshold = 30 seconds. Leave mouse and keyboard alone for 30s...
echo.
python "%~dp0..\app\flipclock_overlay.py" --idle-seconds 30
echo.
echo Host exited. Press any key to close this window.
pause >nul
