@echo off
title Vasco AI Agent Studio
echo ==========================================================
echo   Demarrage de Vasco AI Agent Studio (http://localhost:8000)
echo ==========================================================
start "" "http://localhost:8000"
"%~dp0.venv\Scripts\python.exe" "%~dp0run.py"
pause
