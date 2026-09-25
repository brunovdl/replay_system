# Replay 2.0 - Sistema de Câmera de Quadra para Vôlei

Aplicação web mobile voltada para quadras esportivas que atua como câmera contínua e processador local de visão computacional. O smartphone (montado em tripé no modo horizontal/landscape) grava e mantém um buffer circular de 30 segundos com áudio da quadra.

Ao detectar um jogador com o braço erguido por 3 segundos (ou via botão manual na tela), a aplicação envia os fragmentos para o backend FastAPI. O backend normaliza e concatena o vídeo em MP4 (H.264 + AAC) em segundo plano e despacha para o webhook do **n8n**, que envia para os grupos de WhatsApp via **Evolution API**.

---

## Recursos Principais

* **Buffer Circular Resiliente:** Gravação contínua em blocos autônomos de 5s (mantém últimos 30 segundos).
* **Gatilho por IA (MediaPipe Pose):** Disparo ao manter o punho acima do ombro por 3 segundos consecutivos.
* **Túnel HTTPS Automático (Cloudflare):** Permite acesso direto no Android e iPhone com certificado SSL oficial liberando a câmera sem configurações manuais.
* **Seleção de Câmeras:** Suporte a alternância rápida de lentes (Frontal, Traseira Principal 1x, Ultra-Wide 0.5x).
* **Processamento Assíncrono FFmpeg:** Concatenação e transcodificação rápida em background.
* **Integração n8n & WhatsApp:** Envio automático via webhook multipart para a Evolution API.

---

## Como Executar Localmente

### Opção 1: Arquivos de Inicialização Rápida (Windows)
* Dê duplo clique em `run_local.bat` ou execute no PowerShell:
```powershell
.\run_local.ps1
```
* O sistema cria o ambiente virtual Python, instala dependências e inicia o servidor FastAPI com o túnel HTTPS Cloudflare.
* Abra `http://localhost:8000` no PC ou escaneie o QR Code na interface pelo celular.

### Opção 2: Docker / Docker Compose
```bash
docker compose up --build
```

---

## Estrutura do Projeto

```text
├── frontend/
│   ├── index.html              # Interface do visualizador de câmera e HUD
│   ├── css/
│   │   └── style.css           # Estilos de alto contraste (Landscape mobile)
│   └── js/
│       ├── app.js              # Inicialização, câmera, Wake Lock e orquestração
│       ├── recorder.js         # Buffer circular de blocos autônomos de 5s
│       ├── pose.js             # MediaPipe Tasks Vision e gatilho de 3s
│       ├── audio.js            # Síntese Web Audio (apito sonoro)
│       └── config.js           # Gerenciador de configurações locais
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI app, rotas e static files mount
│   │   ├── converter.py        # Pipeline FFmpeg assíncrono
│   │   ├── tunnel.py           # Gerenciador do túnel Cloudflare HTTPS
│   │   └── n8n_client.py       # Cliente HTTP com retry para o webhook do n8n
│   ├── requirements.txt        # Dependências Python
│   └── .env.example            # Exemplo de variáveis de ambiente
├── docker-compose.yml          # Configuração Docker
├── Dockerfile                  # Imagem base Python + FFmpeg
└── run_local.bat               # Launcher local
```
