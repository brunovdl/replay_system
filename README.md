# 🏐 Replay System — Quadra de Vôlei

Replay instantâneo para a quadra. Ele mantém os últimos 30 segundos de vídeo e,
quando alguém pede, salva o lance e envia para o WhatsApp (via webhook do n8n).

Há duas formas de rodar:

| | 💻 PC (`replay_cam.py`) | 📱 Só celular (`mobile/`) |
|---|---|---|
| Câmera | celular via Iriun, webcam ou câmera IP Xiaomi | qualquer câmera do celular (traseiras, frontal) |
| Onde roda | PC na quadra | Chrome Android + servidor no Easypanel |
| Gatilho | ESPAÇO ou braço levantado por 4s (YOLO) | botão na tela ou braço levantado por 4s (MediaPipe) |
| Internet | só para enviar o replay | 4G, envia só o clipe (~9 MB) |

A versão celular está documentada em [`mobile/README.md`](mobile/README.md).
O resto deste arquivo trata da versão PC.

---

## 💻 Versão PC

### Instalação

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          # e preencha N8N_WEBHOOK_URL
```

Para usar a câmera Xiaomi, copie também `go2rtc.example.yaml` para
`go2rtc.yaml`, preencha com os dados dela e baixe o
[go2rtc.exe](https://github.com/AlexxIT/go2rtc/releases) para esta pasta.

### Uso

Dê dois cliques em **`INICIAR_REPLAY.bat`** e escolha a câmera:

```
   [1] Integrated Camera
   [2] Iriun Webcam (celular)  <- ENTER
   [3] Camera IP Xiaomi (via go2rtc)
```

- **ENTER** repete a última escolha.
- **Iriun:** o launcher abre o app no PC sozinho. Depois é só abrir o Iriun no
  celular.
- **Xiaomi:** o launcher descobre o IP da câmera pelo MAC e sobe o go2rtc.

### Como disparar o replay

| Ação | Efeito |
|---|---|
| `ESPAÇO` (em qualquer janela) | salva os últimos 30s |
| Braço levantado por 4s | salva os últimos 30s. Bipe curto quando começa a contar, dois bipes quando dispara |
| `Q` na janela | encerra |

O replay vai para `replays/replay_zap_AAAAMMDD_HHMMSS.mp4` (H.264 + AAC,
compatível com WhatsApp) e é enviado ao n8n com os campos `file`, `duracao` e
`evento`.

### Configurações (topo do `replay_cam.py`)

| Variável | Padrão | Descrição |
|---|---|---|
| `CAMERA_WIDTH` / `CAMERA_HEIGHT` | 1280x720 | resolução pedida à câmera local |
| `BUFFER_JPEG_QUALITY` | 85 | compressão do buffer (~150 MB de RAM para 30s em 720p) |
| `REPLAY_SECONDS` | 30 | duração do replay |
| `GESTURE_HOLD_SECS` | 4.0 | tempo com o braço levantado para disparar |
| `GESTURE_GRACE_SECS` | 1.0 | tolerância a falhas de detecção durante o hold |
| `GESTURE_SOUND` | True | bipes no PC (para quem está longe da tela) |
| `YOLO_INPUT_SIZE` | 480 | 640 = mais preciso para jogadores distantes, porém mais pesado |
| `YOLO_FPS` | 5 | inferências por segundo (controla o uso de CPU) |
| `YOLO_DEBUG` | False | desenha os esqueletos no preview |

### Arquivos

| Arquivo | Papel |
|---|---|
| `INICIAR_REPLAY.bat` / `iniciar.py` | launcher: menu de câmeras, Iriun, Xiaomi + go2rtc |
| `replay_cam.py` | captura, buffer comprimido, gesto (YOLO pose), gravação, envio ao n8n |
| `cameras.py` | lista as câmeras do Windows e abre o Iriun |
| `Dockerfile` | imagem do servidor da **versão celular** (Easypanel) |
