@echo off
rem Benchforge setup (Windows). ASCII-only on purpose to avoid mojibake.
rem Japanese in .bat breaks under cmd.exe codepage; keep this file ASCII.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo ============================================
echo   Benchforge setup (Windows)
echo ============================================
echo.

rem --- find uv (PATH or standard install location) ---
set "UVCMD="
uv --version >nul 2>nul && set "UVCMD=uv"
if not defined UVCMD if exist "%USERPROFILE%\.local\bin\uv.exe" set "UVCMD=%USERPROFILE%\.local\bin\uv.exe"

rem --- install uv if missing (Python not required beforehand) ---
if not defined UVCMD (
  echo - Installing uv ...
  powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)
if not defined UVCMD if exist "%USERPROFILE%\.local\bin\uv.exe" set "UVCMD=%USERPROFILE%\.local\bin\uv.exe"
if not defined UVCMD (
  echo [ERROR] Failed to install uv. Check your network and retry.
  echo         Behind a corporate proxy you may need HTTPS_PROXY set.
  pause
  exit /b 1
)
echo [OK] using uv:
"%UVCMD%" --version

rem --- create venv (uv downloads Python 3.12 if needed) ---
if not exist ".venv" (
  rem ** if( ) block: parentheses in echo close the block. Do not use ( ) here. **
  echo - Preparing Python environment. Python may be downloaded on first run ...
  "%UVCMD%" venv .venv --python 3.12
  if errorlevel 1 (
    echo [ERROR] Failed to create Python environment. Check network and retry.
    pause
    exit /b 1
  )
)

echo - Installing core dependencies ...
"%UVCMD%" pip install -r requirements.txt --python ".venv\Scripts\python.exe"
if errorlevel 1 (
  echo [ERROR] Failed to install dependencies. Check network and retry.
  pause
  exit /b 1
)

rem --- create config.yaml from template if missing ---
if not exist "config.yaml" (
  copy /y "config.example.yaml" "config.yaml" >nul
  echo [OK] Created config.yaml from template.
  echo      Edit 'results_dir' in config.yaml to point at your results folder.
)

echo.
echo ============================================
echo [DONE] Setup complete.
echo        Double-click Windows_start.bat to launch Benchforge.
echo ============================================
pause
