@echo off
cd /d "%~dp0"
echo ========================================
echo   Multi-Device Monitor
echo ========================================
echo.
python multi_device_monitor.py
echo.
echo Stopped. Press any key to exit...
pause >nul
