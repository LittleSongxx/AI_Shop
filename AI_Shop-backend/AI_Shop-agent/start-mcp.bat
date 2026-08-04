@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo [SmartSelect MCP] Streamable HTTP server...

if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [error] run start.bat first to create .venv and install deps
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import mcp" 1>nul 2>nul
if errorlevel 1 (
    echo [SmartSelect MCP] installing mcp deps...
    call ".venv\Scripts\pip.exe" install -r requirements.lock -q
    if errorlevel 1 (
        echo [error] pip install failed
        pause
        exit /b 1
    )
)

echo [SmartSelect MCP] http://0.0.0.0:7060/mcp
echo [SmartSelect MCP] Ctrl+C to stop
echo.

".venv\Scripts\python.exe" -m app.mcp_server

pause
