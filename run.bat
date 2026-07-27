@echo off
chcp 65001 >nul

echo [WNACG Downloader] 正在检查基本环境...

if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

where uv >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [WNACG Downloader] 未检测到 uv 环境，正在自动下载安装（仅限用户目录，无系统污染）...
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
)

if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

where uv >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo.
    echo [错误] 无法定位或自动安装 uv 命令！
    echo 提示：请检查您的网络连接、系统代理配置，或开启科学上网/全局代理后重启终端重试。
    echo.
    pause
    exit /b 1
)

echo [WNACG Downloader] 正在检查并同步本地隔离环境与依赖库 (零系统污染)...
uv sync
if %ERRORLEVEL% neq 0 (
    echo.
    echo [错误] 依赖库检查或自动同步失败！
    echo 提示：请检查您的网络连接、系统代理配置，或开启科学上网/全局代理后重试。
    echo.
    pause
    exit /b 1
)

echo [WNACG Downloader] 正在启动应用程序...
set PYTHONPATH=%cd%\src
uv run python src/main.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo [错误] 应用程序发生异常并退出。
    pause
    exit /b 1
)

exit /b 0
