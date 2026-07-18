@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo [Simlect Agent] 准备启动...

if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo [提示] 已从 .env.example 生成 .env，请填写 LLM_API_KEY 等配置
    ) else (
        echo [错误] 缺少 .env 配置文件
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [Simlect Agent] 创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建 venv 失败，请确认已安装 Python 3.11+
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\uvicorn.exe" (
    echo [Simlect Agent] 安装依赖（首次较慢）...
    call ".venv\Scripts\pip.exe" install -r requirements-runtime.txt -q
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
)

echo [Simlect Agent] 启动 http://0.0.0.0:7050
echo [Simlect Agent] 健康检查 http://localhost:7050/health
echo [Simlect Agent] 按 Ctrl+C 停止
echo.

".venv\Scripts\uvicorn.exe" app.main:app --host 0.0.0.0 --port 7050 --reload

pause
