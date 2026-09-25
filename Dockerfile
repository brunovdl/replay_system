FROM python:3.11-slim

# Evita prompts interativos durante instalação de pacotes
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Instala FFmpeg e dependências de sistema essenciais
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependências Python primeiro (aproveita cache de build do Docker)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copia código do backend e frontend
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/

# Cria pasta para temporários
RUN mkdir -p /app/scratch/temp_jobs

EXPOSE 8000

# Executa o servidor FastAPI com Uvicorn
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
