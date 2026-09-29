@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set PY=%PYTHON%
if "%PY%"=="" set PY=python

%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo pip install failed - aborting
  exit /b 1
)
%PY% -m src.context_aware_audio.sound_synth
if errorlevel 1 (
  echo sound asset generation failed - aborting
  exit /b 1
)

%PY% -m PyInstaller ^
  --noconfirm --clean --onefile --windowed ^
  --name context_audio ^
  --add-data "assets;assets" ^
  --hidden-import=pygame ^
  --hidden-import=sounddevice ^
  desktop_app.py

if errorlevel 1 (
  echo PyInstaller build failed
  exit /b 1
)

echo.
echo EXE ready: dist\context_audio.exe
pause
