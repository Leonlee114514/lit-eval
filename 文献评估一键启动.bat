@echo off
setlocal
title Lit-Eval Launcher

REM ---- Locate project root (script may sit inside the project, or next to it) ----
set "PROJ="
if exist "%~dp0backend\app\main.py" set "PROJ=%~dp0"
if not defined PROJ for /d %%D in ("%~dp0*") do (
    if exist "%%~fD\backend\app\main.py" set "PROJ=%%~fD\"
)
if defined PROJ goto :found
echo [ERROR] Cannot find project folder backend\app\main.py.
echo         Put this script inside the project folder, or next to it.
pause
exit /b 1
:found

echo =================================================
echo   Lit-Eval  Launcher
echo   Project dir : %PROJ%
echo   Backend FastAPI : http://127.0.0.1:8000
echo   Frontend Vite   : http://localhost:5173
echo =================================================
echo.

REM ---- Backend (port 8000) ----
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [Backend] port 8000 already running, skip
) else (
    echo [Backend] starting FastAPI uvicorn ...
    cd /d "%PROJ%backend"
    start "Lit-Eval-Backend" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --log-level info"
)

REM ---- Frontend (port 5173) ----
netstat -ano | findstr ":5173" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [Frontend] port 5173 already running, skip
) else (
    cd /d "%PROJ%frontend"
    if exist "%PROJ%frontend\node_modules" (
        echo [Frontend] starting Vite dev server ...
        start "Lit-Eval-Frontend" cmd /k "npm run dev"
    ) else (
        echo [Frontend] node_modules missing, running npm install first ...
        start "Lit-Eval-Frontend" cmd /k "npm install && npm run dev"
    )
)

REM ---- Optional dependencies (hints only, non-blocking) ----
netstat -ano | findstr ":7890" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 echo [Hint] Clash proxy (7890) not running - Sci-Hub channel will be skipped
netstat -ano | findstr ":10086" | findstr "LISTENING" >nul 2>&1
if errorlevel 1 echo [Hint] Kimi WebBridge (10086) not running - browser channel will be skipped

echo.
echo Waiting for services ...
timeout /t 15 /nobreak >nul
start "" http://localhost:5173
echo.
echo Done! Browser opened at http://localhost:5173
echo Keep the two service windows (Lit-Eval-Backend / Lit-Eval-Frontend) open.
echo To stop: close those windows. To restart: double-click this script again.
timeout /t 8 /nobreak >nul
exit
