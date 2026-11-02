@echo off
rem Double-click to serve SunShop to the lab (network + Tailscale). Keep this window open.
cd /d "%~dp0"
.venv\Scripts\python serve.py %*
pause
