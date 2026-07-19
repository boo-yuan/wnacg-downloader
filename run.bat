@echo off
chcp 65001 >nul

echo [WNACG Downloader] Checking environment...
where uv >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [Error] uv command not found.
    echo Please install uv by running the following command in PowerShell:
    echo powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    echo After installation, please restart this terminal.
    pause
    exit /b 1
)

echo [WNACG Downloader] Starting application...
set PYTHONPATH=%cd%\src
uv run python src/main.py
exit
