@echo off
rem Build AkaiCDISOMaker.exe in a clean venv (a big global Python makes a multi-GB exe)
cd /d "%~dp0"
python -m venv .buildenv
.buildenv\Scripts\python -m pip install -q numpy scipy soundfile sounddevice pyinstaller
.buildenv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name AkaiCDISOMaker --collect-binaries soundfile --collect-binaries sounddevice --exclude-module matplotlib --exclude-module pandas --exclude-module IPython --exclude-module pytest akai_cd_iso_maker.py
