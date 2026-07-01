@echo off
setlocal

cd /d "%~dp0.."

if not exist "logs" mkdir "logs"
set "WATCHDOG_LOG=logs\watchdog.log"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  echo ERROR: .venv\Scripts\python.exe not found. Create your venv first.
  exit /b 1
)

:loop
echo.
echo [%DATE% %TIME%] Avvio server (Waitress)...
echo.>> "%WATCHDOG_LOG%"
echo [%DATE% %TIME%] Avvio server (Waitress)...>> "%WATCHDOG_LOG%"
%PY% run_waitress.py >> "%WATCHDOG_LOG%" 2>>&1
set "EC=%ERRORLEVEL%"

echo.
echo [%DATE% %TIME%] Server terminato (exit %EC%). Riavvio tra 3 secondi...
echo [%DATE% %TIME%] Server terminato (exit %EC%). Riavvio tra 3 secondi...>> "%WATCHDOG_LOG%"
timeout /t 3 /nobreak >nul

goto loop
