@echo off
echo ====================================================
echo WNACG Downloader - Local Build Script (Nuitka)
echo ====================================================

REM 1. Install required dependencies for build
echo [1/3] Installing build dependencies...
python -m pip install -e .
python -m pip install nuitka pillow zstandard

REM 2. Generate ICO file from PNG for the executable icon
echo [2/3] Generating ICO file from PNG...
python -c "from PIL import Image; Image.open('src/resource/icon.png').save('src/resource/icon.ico', format='ICO', sizes=[(256, 256)])"

REM 3. Compile the executable using Nuitka
echo [3/3] Compiling executable with Nuitka (This might take a while)...
python -m nuitka ^
    --assume-yes-for-downloads ^
    --standalone ^
    --onefile ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=src/resource/icon.ico ^
    --enable-plugin=pyside6 ^
    --include-package=qfluentwidgets ^
    --output-dir=dist ^
    --company-name="boo-yuan" ^
    --product-name="WNACG Downloader" ^
    --file-version=1.0.0 ^
    --product-version=1.0.0 ^
    --file-description="A modern comic download manager" ^
    src/main.py

echo ====================================================
echo Build Complete! Check the 'dist' folder for main.exe
echo ====================================================
pause
