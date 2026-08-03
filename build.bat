@echo off
setlocal
chcp 65001 >nul

echo ==================================================
echo WNACG Downloader - Locked Local Build
echo ==================================================

where uv >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 uv。请先从 https://docs.astral.sh/uv/ 安装 uv。
    exit /b 1
)

echo [1/2] 正在按锁文件同步运行和构建依赖...
uv --cache-dir "%TEMP%\wnacg-downloader-uv-cache" sync --locked --no-dev --group build
if errorlevel 1 exit /b 1

echo [2/2] 正在构建单文件 Windows 程序...
uv --cache-dir "%TEMP%\wnacg-downloader-uv-cache" run --locked --no-dev --group build pyinstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "WNACG-Downloader" ^
    --icon "src/wnacg/resource/icon.ico" ^
    --copy-metadata "wnacg-downloader" ^
    --add-data "src/wnacg/resource;wnacg/resource" ^
    --paths "src" ^
    "src/wnacg/main.py"
if errorlevel 1 exit /b 1

echo 构建完成：dist\WNACG-Downloader.exe
exit /b 0
