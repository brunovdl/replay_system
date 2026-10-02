@echo off
chcp 65001 > nul
title Replay System - Launcher
cd /d "%~dp0"
echo.
echo  Iniciando Replay System...
echo.
call .venv\Scripts\activate.bat
python iniciar.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    pause
)
