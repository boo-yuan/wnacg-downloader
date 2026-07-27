@echo off
chcp 65001 >nul

echo [WNACG Downloader] 正在检查基本环境...

rem 1. 尝试直接检测或从用户默认安装目录加载 uv
where uv >nul 2>nul
if %ERRORLEVEL% equ 0 goto :uv_ready

if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
    set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
)

where uv >nul 2>nul
if %ERRORLEVEL% equ 0 goto :uv_ready

rem 2. 若仍未检测到，则执行自动静默下载安装
echo [WNACG Downloader] 未检测到 uv 环境，正在后台自动下载安装（仅限用户目录，无系统污染）...
powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if %ERRORLEVEL% neq 0 goto :install_error

if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
    set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
)

where uv >nul 2>nul
if %ERRORLEVEL% neq 0 goto :locate_error

:uv_ready
rem 3. 检查并同步本地独立依赖环境
echo [WNACG Downloader] 正在检查并同步本地隔离环境与依赖库 (零系统污染)...
uv sync
if %ERRORLEVEL% neq 0 goto :sync_error

rem 4. 启动主程序
echo [WNACG Downloader] 正在启动应用程序...
set PYTHONPATH=%cd%\src
uv run python src/main.py
if %ERRORLEVEL% neq 0 goto :run_error
exit /b 0

:install_error
echo.
echo [错误] uv 自动下载或安装失败！
echo 提示：请检查您的网络连接、系统代理配置，或开启科学上网/全局代理后重试。
echo.
pause
exit /b 1

:locate_error
echo.
echo [错误] 安装完成但仍无法定位 uv.exe！请尝试重启当前终端后重新运行。
echo.
pause
exit /b 1

:sync_error
echo.
echo [错误] 依赖库检查或自动同步失败！
echo 提示：请检查您的网络连接、系统代理配置，或开启科学上网/全局代理后重试。
echo.
pause
exit /b 1

:run_error
echo.
echo [错误] 应用程序发生异常并退出。
pause
exit /b 1
