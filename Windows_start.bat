@echo off
rem Benchforge launcher (Windows). ASCII-only to avoid mojibake; keep it ASCII.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] No virtual environment found. Run Windows_setup.bat first.
  pause
  exit /b 1
)

if not exist "config.yaml" (
  copy /y "config.example.yaml" "config.yaml" >nul
)

echo Starting Benchforge ...
".venv\Scripts\python.exe" run.py %*
if errorlevel 1 (
  echo.
  echo Benchforge exited with an error. See the message above.
  pause
)
