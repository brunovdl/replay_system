@echo off
title Replay 2.0 - Servidor Local
chcp 65001 > nul
echo =======================================================
echo          REPLAY 2.0 - SISTEMA DE CAMERA DE QUADRA
echo =======================================================
echo.

cd /d "%~dp0"

REM Verifica se o ambiente virtual existe
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Criando ambiente virtual Python...
    python -m venv .venv
    echo [INFO] Instalando dependencias...
    .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
)

echo [INFO] Iniciando servidor FastAPI local com túnel HTTPS automático...
echo.
echo =======================================================
echo  ACESSO NO PC:
echo    -^> http://localhost:8000
echo.
echo  ACESSO NO CELULAR (ANDROID / IPHONE COM CÂMERA):
echo    O túnel HTTPS Cloudflare será gerado automaticamente.
echo    Clique no ícone de Celular 📱 na barra superior da tela
echo    ou aponte a câmera pro QR Code gerado!
echo.
echo  Pressione CTRL+C para encerrar o servidor
echo =======================================================
echo.

REM Inicia o navegador padrao no localhost apos 2 segundos em background
start "" /b cmd /c "timeout /t 2 /nobreak > nul && start http://localhost:8000"

REM Executa o Uvicorn
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload

pause
