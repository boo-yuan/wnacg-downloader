@echo off
setlocal
chcp 65001 >nul

echo [WNACG Downloader] 正在检查运行环境...
where uv >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 uv。请先从 https://docs.astral.sh/uv/ 安装 uv。
    exit /b 1
)

echo [WNACG Downloader] 正在按锁文件同步依赖...
uv --cache-dir "%TEMP%\wnacg-downloader-uv-cache" sync --locked
if errorlevel 1 (
    echo [错误] 依赖同步失败，请检查网络和代理配置。
    exit /b 1
)

echo [WNACG Downloader] 正在启动应用程序...
uv --cache-dir "%TEMP%\wnacg-downloader-uv-cache" run --locked wnacg-downloader %*
if errorlevel 1 (
    echo [错误] 应用程序异常退出。
    exit /b 1
)

exit /b 0
