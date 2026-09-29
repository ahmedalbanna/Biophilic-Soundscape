@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set PY=%PYTHON%
if "%PY%"=="" set PY=python

if not exist assets\water_stream.wav (
  %PY% -m src.context_aware_audio.sound_synth
)

%PY% -m src.context_aware_audio.app
if errorlevel 1 pause
