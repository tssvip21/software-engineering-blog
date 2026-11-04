@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run scripts\setup.ps1 first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 "scripts\run_local.py" --demo
pause
