@echo off
rem Used by the "SunShop" scheduled task (runs at boot, no window). Output goes to logs\server.log.
rem To run the server by hand in a window instead, use start_sunshop.bat.
cd /d "%~dp0"
if not exist logs mkdir logs
echo. >> logs\server.log
echo ===== Starting %date% %time% ===== >> logs\server.log
.venv\Scripts\python.exe -u serve.py %* >> logs\server.log 2>&1
