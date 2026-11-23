@echo off
title Vasco AI - Hebergement Public HTTPS
echo =====================================================================
echo   Demarrage de Vasco AI + Tunnel Public HTTPS (Auto-Reconnect)
echo =====================================================================
start "Vasco Backend" /min "%~dp0.venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
timeout /t 2 /nobreak >nul
:loop
echo Generation de votre lien public HTTPS en cours...
ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R 80:127.0.0.1:8000 nokey@localhost.run
echo Reconnexion du tunnel dans 3 secondes...
timeout /t 3 /nobreak >nul
goto loop
