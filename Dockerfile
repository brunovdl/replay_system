# Dockerfile para deploy no Easypanel
FROM python:3.11-slim

# Evita buffering no stdout/stderr e gravação de .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Instala ffmpeg nativo no sistema e dependências básicas
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia código da aplicação
COPY . .

# Garante existência do diretório de saída
RUN mkdir -p replays

# Porta exposta para o proxy reverso do Easypanel (Traefik)
EXPOSE 8000

# Execução da aplicação FastAPI
CMD ["python", "app.py"]
