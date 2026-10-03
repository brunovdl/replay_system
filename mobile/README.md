# 📱 Replay Quadra — versão celular

Replay usando **só o celular**. O celular filma e guarda os últimos 30s já
comprimidos. Quando alguém aperta **REPLAY** ou **levanta o braço por 3s**, só o clipe (~9 MB) é enviado ao
servidor, que gera o MP4 do WhatsApp e encaminha ao n8n.

```
Celular (Chrome Android)                          Easypanel (este container)
  câmera → H.264 do hardware → buffer 30s  ──clipe──>  ffmpeg → MP4 do zap ──> n8n ──> WhatsApp
```

O sistema do PC (`../replay_cam.py`) continua funcionando normalmente.

---

## 🚀 Deploy no Easypanel

O `Dockerfile` fica na **raiz** do repositório, como no antigo Replay 2.0.

1. **Serviço:**
   - Se o serviço do Replay 2.0 já existe no Easypanel, use o mesmo e só faça
     *Deploy* de novo.
   - Senão, vá no projeto → *+ Service* → *App* → GitHub `brunovdl/replay_system`,
     branch `main`, Build Path `/`, tipo *Dockerfile*.
2. **Variáveis de ambiente** (aba *Environment*):

   | Variável | Valor |
   |---|---|
   | `REPLAY_TOKEN` | **nova**: uma senha forte, pedida na tela do celular |
   | `N8N_WEBHOOK_URL` | URL do webhook do n8n. A `WEBHOOK_URL` do serviço antigo também funciona |
   | `KEEP_DAYS` | `7` (apaga replays antigos do servidor; `0` = nunca) |
   | `MAX_STORAGE_GB` | `5` (acima disso apaga os mais antigos; `0` = sem limite) |

3. **Volume** (aba *Mounts*): volume montado em **`/data`**. Sem ele, os
   replays e a telemetria somem a cada deploy.
4. **Domínio** (aba *Domains*): porta **8000**, com HTTPS. O Chrome **só libera
   a câmera em HTTPS**.
5. Abra `https://<seu-domínio>/api/health`. Deve responder
   `{"ok":true,"ffmpeg":true,"n8n":true,"senha":true}`.

O webhook do n8n recebe **exatamente os mesmos campos** que o PC envia hoje
(`file`, `duracao`, `evento`), então o fluxo do n8n não muda. O `evento` vem
como `botao`.

---

## 📲 Uso no celular

1. Abra o domínio no **Chrome** → digite a senha → **Começar a filmar**.
2. Permita a câmera. A página entra em tela cheia, deitada.
3. Opcional: menu ⋮ → *Adicionar à tela inicial*, para abrir como app.
4. Para trocar de câmera, toque em **📷 Câmera**. A lista mostra todas as
   câmeras que o Chrome enxerga: traseiras (principal e grande-angular, se o
   celular as expõe) e frontal. A escolha fica salva para a próxima vez.
   Trocar de câmera reinicia o buffer.
5. Espere a barra do buffer ficar verde (30s) e toque em **REPLAY**.
6. Acompanhe o envio no canto inferior esquerdo:
   *enviando → gerando vídeo → ✔ Enviado para o WhatsApp*.

### 📁 Replays salvos

Cada replay fica guardado no volume `/data` do servidor. Para ver, baixar ou
apagar, abra **`https://<seu-domínio>/arquivos.html`** (ou toque em
*📁 Ver e apagar replays salvos* na tela inicial). A senha é a mesma.

- ▶ assiste, ⬇ baixa, 🗑 apaga. Dá para selecionar vários ou apagar todos.
- Mostra o espaço usado, o limite e o espaço livre no servidor.
- Itens com ⚠ são clipes que falharam na conversão (ficam para diagnóstico).
- Limpeza automática: depois de `KEEP_DAYS` dias, ou quando passa de
  `MAX_STORAGE_GB`, o servidor apaga os mais antigos sozinho.

⚠️ **A página precisa ficar aberta e na frente.** Se trocar de app ou bloquear
a tela, a câmera pausa. A tela fica ligada sozinha (Wake Lock).

---

## 🧪 Teste de campo (objetivo da Fase 1)

Leve o celular no tripé, **ligado no power bank**, e filme uma partida inteira.

| Verificar | Onde ver | Bom sinal |
|---|---|---|
| FPS estável | barra do topo | ~30 fps (aceitável ≥ 24) |
| Frames descartados | barra do topo (só aparece se houver) | zero ou quase |
| Bateria | barra do topo | sobe ou se mantém no power bank |
| Aquecimento | mão no celular | morno, sem aviso de temperatura |
| Envio no 4G | lista de envios | ✔ em menos de ~1 min |
| Qualidade | vídeo no WhatsApp | dá para ver a jogada |

A cada minuto, a página registra fps, bateria e descartes em
`/data/telemetria.jsonl` no servidor. Depois do teste, esse arquivo mostra
como o celular se comportou ao longo da partida.

### Gatilho por gesto

Qualquer jogador que **mantenha o braço levantado por 3s** dispara o replay.
O braço conta como levantado quando o pulso (ou o cotovelo) fica acima da
cabeça. Com o jogador de costas, vale o pulso bem acima do ombro.

- **Bipe curto + vibração:** o hold começou. Uma contagem grande aparece na tela.
- **Dois bipes + borda verde:** replay disparado.
- Falhas de detecção de até 1s não cancelam a contagem. Depois de cada
  disparo há 5s de pausa.
- A detecção roda **no celular** (MediaPipe Pose, na GPU), 5 vezes por
  segundo. Na primeira vez, o celular baixa ~18 MB, que depois ficam no cache.
- **🙋 Gesto: ON/OFF** liga e desliga o gesto. O botão REPLAY sempre funciona.
- A barra do topo mostra quantas pessoas o detector está vendo. Se ficar em
  0 com gente na quadra, a câmera está longe ou baixa demais.

Ajustes no topo de `static/gesto.js` (`GESTO`): `HOLD_SECS` (tempo do hold),
`GRACE_SECS`, `COOLDOWN_SECS`, `FPS`, `VISIBILIDADE`.

### Ajustes rápidos (topo de `static/app.js`, em `CFG`)

- `BITRATE`: 2,5 Mbps por padrão. Se o vídeo ficar borrado, use 4 Mbps (clipe
  de ~15 MB). Se o envio no 4G ficar lento, use 1,5 Mbps.
- `REPLAY_SECONDS`: duração do replay.
- `WIDTH`/`HEIGHT`: 1280x720 por padrão.

---

## Próximas fases

- **Fase 4:** fila offline persistente (o clipe sobrevive mesmo se a página
  recarregar), tela de status e QR code para abrir a página.

## Arquivos

| Arquivo | Papel |
|---|---|
| `app.py` | servidor FastAPI: recebe o clipe, ffmpeg, envia ao n8n, telemetria |
| `static/app.js` | captura, codificação H.264, buffer de 30s, troca de câmera, envio com novas tentativas |
| `static/gesto.js` | gesto do braço levantado: MediaPipe Pose, regra e contagem do hold |
| `static/index.html` | interface (tela inicial + filmagem) |
| `static/arquivos.html` / `arquivos.js` | gestão dos replays salvos no servidor |
| `../Dockerfile` | imagem com Python + ffmpeg, para o Easypanel (fica na raiz) |
