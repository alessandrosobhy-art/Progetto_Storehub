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

echo.>> "%WATCHDOG_LOG%"
echo [%DATE% %TIME%] Verifica dipendenze ambiente...>> "%WATCHDOG_LOG%"
%PY% -c "import flask_session, flask_wtf, flask_limiter, flask_compress" >nul 2>&1
if errorlevel 1 (
  echo [%DATE% %TIME%] Dipendenze mancanti: avvio installazione da requirements.txt...
  echo [%DATE% %TIME%] Dipendenze mancanti: avvio installazione da requirements.txt...>> "%WATCHDOG_LOG%"
  %PY% -m pip install -r requirements.txt >> "%WATCHDOG_LOG%" 2>>&1
  if errorlevel 1 (
    echo [%DATE% %TIME%] Installazione dipendenze fallita. Nuovo tentativo tra 10 secondi...
    echo [%DATE% %TIME%] Installazione dipendenze fallita. Nuovo tentativo tra 10 secondi...>> "%WATCHDOG_LOG%"
    timeout /t 10 /nobreak >nul
    goto :eof
  )
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
