# Especificação de Projeto: Sistema de Replay Web para Vôlei (Replay 2.0)

## 1. Visão Geral
Aplicação web mobile voltada para quadras esportivas que atua como câmera contínua e processador local de visão computacional. O smartphone (montado em tripé no modo horizontal/landscape) grava e mantém um buffer circular de 30 segundos com áudio da quadra. Ao detectar um jogador com o braço erguido por 3 segundos (ou via botão manual na tela), a aplicação envia os fragmentos para o backend FastAPI. O backend normaliza e concatena o vídeo em MP4 (H.264 + AAC) em segundo plano e despacha para o webhook do **n8n**, que envia para os grupos de WhatsApp via **Evolution API**.

---

## 2. Decisões Técnicas Principais (Acordo Técnico)

*   **Ambiente e Execução:** 
    *   Backend FastAPI servindo os arquivos estáticos do frontend (mesma origem, zero problemas de CORS).
    *   Desenvolvimento e testes locais, com deploy de produção em container Docker no **Easypanel** com SSL automático.
*   **Buffer de Vídeo Resiliente:** 
    *   Gravação contínua em mini-clipes autônomos de 5s completos com cabeçalho (mantendo os últimos 6 clipes = 30s de buffer). Evita a corrupção do container WebM/MP4 ao descartar frames antigos.
    *   Concatenação e transcodificação no backend via FFmpeg (`libx264` + `aac` + `movflags +faststart`).
*   **Visão Computacional e Gatilho (MediaPipe):**
    *   MediaPipe Tasks Vision (`PoseLandmarker`) com taxa de amostragem reduzida para 8-10 FPS (otimiza bateria e evita superaquecimento do celular sob sol).
    *   Disparo ao manter o punho acima do ombro (`wrist.y < shoulder.y`) por 3.0 segundos consecutivos em qualquer jogador visível.
    *   Cooldown de 15 segundos na tela pós-disparo com contagem regressiva visível.
    *   Botão flutuante "Replay Manual" para acionamento instantâneo de backup.
*   **Feedback na Quadra:**
    *   HUD de alto contraste visível sob luz solar.
    *   Anel de progresso visual (3s) + apito sonoro duplo sintetizado (Web Audio API) + vibração háptica no celular.
*   **Processamento Assíncrono:**
    *   FastAPI responde `200 OK` instantaneamente (< 300ms) liberando o celular para continuar gravando o rali seguinte.
    *   Conversão FFmpeg e repasse ao n8n executados em `BackgroundTasks`.
*   **Integração n8n e WhatsApp (Validado via MCP):**
    *   Workflow n8n `Replay Volei` (`icIyP8ldcBwrO5kq`) ativo em: `https://geral.n8n.p2me0s.easypanel.host/webhook/replay`.
    *   Envio multipart/form-data com campos: `file` (vídeo .mp4), `duracao` ("30s") e `evento` ("Gatilho de Pose" ou "Disparo Manual").
    *   O nó `Monta Payload` do n8n processa o binário e entrega para os nós Evolution API (`120363412495367285@g.us` e `5516991847619@s.whatsapp.net`).

---

## 3. Arquitetura de Pastas Sugerida

```text
/replay2.0
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
│   │   └── n8n_client.py       # Cliente HTTP com retry para o webhook do n8n
│   ├── requirements.txt        # fastapi, uvicorn, python-multipart, httpx
│   ├── Dockerfile              # Imagem base Python 3.11-slim + ffmpeg
│   └── .env.example            # WEBHOOK_URL=https://geral.n8n.p2me0s.easypanel.host/webhook/replay
├── docker-compose.yml          # Para teste local e Easypanel
└── spec.md                     # Esta especificação
```