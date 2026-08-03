@echo off
echo ==================================================
echo WNACG Downloader - Local Build Script
echo ==================================================
echo.

echo [1/3] Installing dependencies...
python -m pip install --upgrade pip
pip install .
pip install pyinstaller pillow

echo.
echo [2/3] Preparing resources...
python -c "from PIL import Image; Image.open('src/resource/icon.png').save('src/resource/icon.ico', format='ICO', sizes=[(256, 256)])"

echo.
echo [3/3] Building executable...
pyinstaller --noconfirm --onefile --windowed --icon "src/resource/icon.ico" --add-data "src/resource;resource" --name "WNACG-Downloader" --paths src src/main.py

echo.
echo ==================================================
echo Build Complete! 
echo You can find WNACG-Downloader.exe in the 'dist' folder.
echo ==================================================
exit