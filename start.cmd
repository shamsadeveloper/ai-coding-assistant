@echo off
rem Starts the API and the web UI on http://127.0.0.1:8001 (port 8000 is used by Laragon on this machine).
rem Use 127.0.0.1 rather than "localhost": on Windows "localhost" tries IPv6 first and every
rem request then waits about 0.2 s before falling back to IPv4, which makes the UI feel slow.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo The virtual environment is missing. Run setup.cmd first.
    exit /b 1
)
echo Web UI:   http://127.0.0.1:8001/ui/
echo API docs: http://127.0.0.1:8001/docs
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 %*
