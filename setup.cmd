@echo off
rem One-time setup: Python environment, packages, database, and the web UI.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    python -m venv .venv || exit /b 1
)
.venv\Scripts\python.exe -m pip install -q --upgrade pip
.venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt || exit /b 1

if not exist .env copy .env.example .env >nul

echo Applying database migrations...
.venv\Scripts\alembic.exe upgrade head || exit /b 1

where npm >nul 2>nul
if %errorlevel%==0 (
    echo Building the web UI...
    pushd frontend
    call npm install --no-audit --no-fund || (popd & exit /b 1)
    call npm run build || (popd & exit /b 1)
    popd
) else (
    echo npm not found: skipping the web UI. Install Node.js to build it.
)

echo.
echo Setup complete. Make sure Ollama is running and the model is pulled:
echo     ollama pull qwen2.5-coder:7b
echo Then start the assistant with:  start.cmd
