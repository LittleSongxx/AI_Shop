@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo [SmartSelect Agent] preparing...

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
    echo [SmartSelect Agent] creating venv...
    python -m venv .venv
    if errorlevel 1 (
        echo [error] venv failed - need Python 3.11+
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\uvicorn.exe" (
    echo [SmartSelect Agent] installing deps first time, may be slow...
    call ".venv\Scripts\pip.exe" install -r requirements.lock -q
    if errorlevel 1 (
        echo [error] pip install failed
        pause
        exit /b 1
    )
)

echo [SmartSelect Agent] http://0.0.0.0:7050
echo [SmartSelect Agent] health http://localhost:7050/health
echo [SmartSelect Agent] Ctrl+C to stop
echo.

".venv\Scripts\uvicorn.exe" app.main:app --host 0.0.0.0 --port 7050 --reload

pause
