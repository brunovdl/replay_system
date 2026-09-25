# 🎬 Replay Web — Sistema de Replay Instantâneo na Nuvem

Sistema de gravação contínua com buffer circular (15s / 30s / 60s) acessível via navegador web no celular ou desktop, com salvamento instantâneo e envio automático para o WhatsApp via webhook do N8N.

Projetado para rodar em containers na nuvem via **EasyPanel**, Docker ou localmente.

---

## ✨ Funcionalidades

- 📱 **100% Web / Mobile-first**: Acessível de qualquer smartphone sem necessidade de instalar aplicativos.
- 🔄 **Buffer Circular em Memória**: Grava continuamente mantendo apenas os últimos N segundos configurados.
- ⚡ **Conversão Instantânea H.264/AAC**: Codificação com `+faststart` totalmente compatível com WhatsApp.
- 📲 **Integração WhatsApp / N8N**: Disparo do vídeo por Webhook multipart com métricas de duração e evento.
- 🌐 **Pronto para Nuvem (Easypanel)**: Dockerfile pré-configurado com FFmpeg nativo e suporte a proxy reverso Traefik.
- 🔒 **HTTPS & WebSockets**: Suporte a SSL público automático (Let's Encrypt no Easypanel) ou certificado local em desenvolvimento.

---

## 🚀 Deploy no EasyPanel

### Método 1: Via Git Repository (Recomendado)
1. Crie um novo **App Service** no seu projeto do EasyPanel.
2. Em **Source**, selecione **Git** e aponte para o seu repositório no GitHub.
3. Em **Build**, o EasyPanel detectará o `Dockerfile` automaticamente.
4. Em **Environment**, configure:
   - `PORT`: `8000`
5. Em **Domains**, adicione o domínio desejado (ex: `replay.seudominio.com`) com HTTPS habilitado.

---

## 💻 Execução Local

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Iniciar o servidor
python app.py
```

O servidor iniciará automaticamente em `https://localhost:8443` (ou no IP da sua rede local).

---

## ⚙️ Configurações

As configurações podem ser ajustadas pelo menu de engrenagem na interface web ou via arquivo `settings.json`:

| Campo | Padrão | Descrição |
|---|---|---|
| `replay_seconds` | `30` | Duração do buffer circular em segundos (15, 30 ou 60) |
| `n8n_webhook_url` | `""` | URL do webhook do N8N para envio ao WhatsApp |
| `camera_mode` | `"native"` | Câmera do celular (`native`) ou externa via URL (`external`) |
| `output_dir` | `"replays"` | Pasta de armazenamento temporário dos replays |

---

## 📦 Estrutura do Projeto

```text
├── app.py                # Servidor FastAPI com WebSocket e rotas REST
├── replay_buffer.py      # Gerenciador de buffer circular e pipeline FFmpeg/N8N
├── static/
│   └── index.html        # Interface Web moderna dark mode (Mobile-first)
├── replays/              # Diretório de armazenamento dos vídeos gerados
├── Dockerfile            # Imagem de container otimizada para o Easypanel
├── requirements.txt      # Dependências Python do projeto
├── .gitignore            # Arquivos ignorados pelo Git
└── README.md             # Documentação do projeto
```