@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo [SmartSelect Agent Worker] starting...

if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo [hint] copied .env.example to .env - fill LLM_API_KEY etc.
    ) else (
        echo [error] missing .env
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [error] run start.bat first to create the virtual environment
    pause
    exit /b 1
)

if not exist ".venv\Scripts\ai-shop-agent-worker.exe" (
    echo [SmartSelect Agent Worker] installing project...
    call ".venv\Scripts\pip.exe" install -r requirements.lock -q
    call ".venv\Scripts\pip.exe" install --no-deps --editable . -q
    if errorlevel 1 (
        echo [error] dependency installation failed
        pause
        exit /b 1
    )
)

echo [SmartSelect Agent Worker] heartbeat key: mall:agent:worker:heartbeat
echo [SmartSelect Agent Worker] Ctrl+C to stop
echo.

".venv\Scripts\ai-shop-agent-worker.exe"

pause
