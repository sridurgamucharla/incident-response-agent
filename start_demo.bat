@echo off
REM Starts the whole DéjàVu demo, each service in its own window.
REM Run from the project root:  start_demo.bat
cd /d "%~dp0"
set PY=%~dp0.venv\Scripts\python.exe

start "ShopLite :8001"  cmd /k "%PY% -m uvicorn shoplite.app:app --port 8001 --no-access-log"
start "Agent :8000"     cmd /k "%PY% -m uvicorn agent.main:app --port 8000"
timeout /t 4 /nobreak >nul
start "Traffic"         cmd /k "%PY% -m shoplite.traffic --rps 6"
start "Sentinel"        cmd /k "%PY% -m sentinel.watcher"
start "Dashboard :5173" cmd /k "cd web && npm run dev"
timeout /t 6 /nobreak >nul
start "" http://localhost:5173
