@echo off
setlocal

cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  echo ERROR: .venv\Scripts\python.exe not found. Create your venv first.
  exit /b 1
)

%PY% run_waitress.py

endlocal
