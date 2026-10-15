@echo off
cd /d "%~dp0"
py -3 -m pip install pyinstaller
if errorlevel 1 goto fail
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name HappyHorseStudio --add-data "web:web" server.py
if errorlevel 1 goto fail
echo Built: dist\HappyHorseStudio.exe
pause
exit /b 0
:fail
echo Build failed. See the error above.
pause
exit /b 1
