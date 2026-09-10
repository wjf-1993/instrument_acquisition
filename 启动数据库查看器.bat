@echo off
chcp 65001 >nul
title 仪器数据采集系统 - 数据库查看器
cd /d "e:\SOLO\Multi Agent\instrument_acquisition"
echo.
echo   正在检查依赖...
"C:\Users\20160144\AppData\Local\Programs\Python\Python312\python.exe" -m pip install -q "protobuf<5.0.0" 2>nul
echo.
echo   正在启动数据库查看器...
echo   浏览器访问: http://172.17.76.7:8501
echo   按 Ctrl+C 停止
echo.
"C:\Users\20160144\AppData\Local\Programs\Python\Python312\python.exe" -m streamlit run db_viewer.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
pause
