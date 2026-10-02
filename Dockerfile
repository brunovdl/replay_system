# Servidor do Replay Quadra (versao celular) — usado pelo Easypanel.
# Fica na raiz para o servico existente no Easypanel continuar buildando sem mudar config.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# ffmpeg com libx264 (gera o MP4 compativel com WhatsApp)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY mobile/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mobile/app.py .
COPY mobile/static ./static

ENV DATA_DIR=/data
# Confia nos cabecalhos X-Forwarded-* do proxy do Easypanel (Traefik).
# Via variavel (lida pelo uvicorn) para o "*" nao virar curinga do shell.
ENV FORWARDED_ALLOW_IPS=*
VOLUME /data
EXPOSE 8000

# PORT: o servico antigo (Replay 2.0) no Easypanel ja usava essa variavel.
# exec: o uvicorn vira o processo principal e recebe o SIGTERM do deploy,
# terminando os replays em andamento em vez de ser morto apos 10s.
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers
