@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python multi_device_monitor.py
pause