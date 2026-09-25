# Script PowerShell para iniciar o Replay 2.0 localmente
$Host.UI.RawUI.WindowTitle = "Replay 2.0 - Servidor Local"

Set-Location $PSScriptRoot

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "        REPLAY 2.0 - SISTEMA DE CAMERA DE QUADRA" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "[INFO] Criando ambiente virtual Python..." -ForegroundColor Yellow
    python -m venv .venv
    Write-Host "[INFO] Instalando dependências..." -ForegroundColor Yellow
    & .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
}

# Obtem o IP local da maquina para teste no celular
$localIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch 'Loopback|vEthernet' -and $_.IPAddress -notmatch '^169\.254\.' } | Select-Object -First 1).IPAddress
if (-not $localIp) { $localIp = "192.168.1.137" }

Write-Host "[OK] Ambiente virtual e dependências validadas." -ForegroundColor Green
Write-Host ""
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host " ACESSO NO PC:" -ForegroundColor White
Write-Host "   -> http://localhost:8000" -ForegroundColor Green
Write-Host ""
Write-Host " ACESSO NO CELULAR (COM CÂMERA LIBERADA):" -ForegroundColor White
Write-Host "   O sistema inicia automaticamente o túnel HTTPS Cloudflare." -ForegroundColor Cyan
Write-Host "   Basta clicar no botão de Celular 📱 no topo da tela" -ForegroundColor Yellow
Write-Host "   ou escanear o QR Code gerado na interface do Replay!" -ForegroundColor Yellow
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host ""

# Abre navegador local automaticamente
Start-Process "http://localhost:8000"

# Inicia o servidor uvicorn
& .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
