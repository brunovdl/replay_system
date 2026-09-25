@echo off
title Replay Mobile — Servidor
color 0A
echo.
echo  ============================================
echo   REPLAY MOBILE — Servidor FastAPI
echo  ============================================
echo.

:: Ativa o ambiente virtual se existir
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

:: Instala dependências mobile se necessário
pip show fastapi >nul 2>&1 || (
    echo Instalando dependencias...
    pip install -r requirements_mobile.txt
)

echo  Iniciando servidor...
echo.
python app.py

pause
