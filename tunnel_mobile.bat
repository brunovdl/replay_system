@echo off
title Replay 2.0 - Tunel HTTPS para Celular (Cloudflare SSL)
chcp 65001 > nul
echo =======================================================
echo    REPLAY 2.0 - LINK HTTPS SEGURO PARA CELULAR (ANDROID / IPHONE)
echo =======================================================
echo.
echo Gerando túnel HTTPS com certificado SSL oficial via Cloudflare...
echo Isso permite que a câmera do Android funcione sem nenhum bloqueio!
echo.
echo O servidor local (run_local.bat) DEVE estar rodando na porta 8000.
echo.
echo =======================================================
echo.

cd /d "%~dp0"

if exist "cloudflared.exe" (
    echo [INFO] Usando Cloudflare Tunnel nativo...
    echo Procure abaixo pela linha que contém "trycloudflare.com" e
    echo abra esse link no navegador do celular:
    echo.
    .\cloudflared.exe tunnel --url http://localhost:8000
) else (
    echo [INFO] Usando localtunnel via npx...
    npx --yes localtunnel --port 8000
)

pause
