@echo off
rem ---------------------------------------------------------------
rem  Flip Clock Overlay - stop host and remove runtime data
rem  Deletes app\.edge-profile (browser cache) plus runtime files
rem  and test logs. Keeps config.json / theme.json / your settings.
rem ---------------------------------------------------------------
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH. Install Python 3 or edit this file.
  pause >nul
  exit /b 1
)
python "%~dp0app\flipclock_overlay.py" --stop >nul 2>nul
rmdir /s /q "%~dp0app\.edge-profile" 2>nul
del /q "%~dp0app\runtime.json" 2>nul
del /q "%~dp0app\overlay.pid" 2>nul
del /q "%~dp0app\*.log" 2>nul
del /q "%~dp0tests\*.log" 2>nul
rmdir /s /q "%~dp0tests\ios-shots" 2>nul
del /q "%~dp0tests\selftest-*.png" 2>nul
del /q "%~dp0tests\selftest-idle.txt" 2>nul
rmdir /s /q "%~dp0tools\__pycache__" 2>nul
echo Cleaned: host stopped, browser cache and runtime files removed.
echo Press any key to close.
pause >nul
