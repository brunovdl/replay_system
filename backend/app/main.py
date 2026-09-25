import os
import uuid
import shutil
import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Garante MIME types corretos no Windows
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")

from fastapi import FastAPI, File, UploadFile, Form, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from dotenv import load_dotenv

import threading
from .converter import concatenate_and_convert, get_ffmpeg_executable
from .n8n_client import dispatch_to_n8n, DEFAULT_WEBHOOK_URL
from .tunnel import start_tunnel, stop_tunnel, get_tunnel_info

# Carrega variáveis de ambiente
load_dotenv()

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("replay.main")

# Diretório base
BASE_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
TEMP_BASE_DIR = BASE_DIR / "scratch" / "temp_jobs"
TEMP_BASE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Replay 2.0 - Vôlei Court Camera Backend",
    version="2.0.0",
    description="Backend para recepção de buffer de vídeo, transcodificação FFmpeg e despacho n8n/WhatsApp"
)

# Habilita CORS para permitir conexões de qualquer dispositivo/celular
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def process_and_dispatch_replay(
    job_dir: Path,
    chunk_paths: List[Path],
    duracao: str,
    evento: str,
    webhook_url: Optional[str]
):
    """
    Executa em segundo plano:
    1. Concatenação e transcodificação FFmpeg para MP4 (H.264 + AAC + faststart).
    2. Envio multipart ao webhook do n8n (repassando para Evolution API / WhatsApp).
    3. Limpeza total dos arquivos temporários.
    """
    logger.info(f"Iniciando processamento em background para {job_dir.name} ({len(chunk_paths)} fragmentos)")
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_mp4 = job_dir / f"Replay_{now_str}.mp4"

    try:
        # 1. Transcodifica e concatena
        concatenate_and_convert(chunk_paths, output_mp4)

        # 2. Despacha para n8n
        result = await dispatch_to_n8n(
            video_path=output_mp4,
            duracao=duracao,
            evento=evento,
            webhook_url=webhook_url
        )
        logger.info(f"Resultado do despacho n8n para {job_dir.name}: {result}")

    except Exception as e:
        logger.error(f"Falha no processamento do replay {job_dir.name}: {e}", exc_info=True)
    finally:
        # 3. Limpeza garantida do diretório do job
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.info(f"Diretório temporário {job_dir.name} removido com sucesso.")
        except Exception as err:
            logger.warning(f"Erro ao remover pasta temporária {job_dir}: {err}")

@app.on_event("startup")
async def on_startup():
    """Inicia o túnel Cloudflare automaticamente em background para celulares"""
    def _bg_start():
        logger.info("Iniciando túnel HTTPS Cloudflare para conexões mobile...")
        start_tunnel(port=8000, wait_for_url=True, timeout=12)

    threading.Thread(target=_bg_start, daemon=True).start()

@app.on_event("shutdown")
async def on_shutdown():
    """Encerra o túnel ao finalizar o backend"""
    stop_tunnel()

@app.get("/api/health")
async def health_check():
    """Verifica status do servidor, FFmpeg, webhook e túnel HTTPS"""
    ffmpeg_ok = False
    ffmpeg_path = ""
    try:
        ffmpeg_path = get_ffmpeg_executable()
        ffmpeg_ok = True
    except Exception as e:
        ffmpeg_path = str(e)

    configured_webhook = os.getenv("WEBHOOK_URL", DEFAULT_WEBHOOK_URL)
    tunnel_data = get_tunnel_info()

    return {
        "status": "online",
        "service": "Replay 2.0 Camera System",
        "ffmpeg": {
            "available": ffmpeg_ok,
            "path": ffmpeg_path
        },
        "n8n_webhook": configured_webhook,
        "tunnel": tunnel_data,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/tunnel-info")
async def tunnel_info_endpoint():
    """Retorna dados do túnel HTTPS para conexões seguras no celular"""
    return get_tunnel_info()

@app.post("/api/tunnel-start")
async def tunnel_start_endpoint():
    """Inicia ou reativa o túnel HTTPS em segundo plano"""
    start_tunnel(port=8000, wait_for_url=True, timeout=15)
    return get_tunnel_info()

@app.post("/api/upload-replay")
async def upload_replay(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    duracao: str = Form("30s"),
    evento: str = Form("Gatilho de Pose (Braço Erguido)"),
    webhook_url: Optional[str] = Form(None)
):
    """
    Recebe os fragmentos de vídeo do smartphone (ex: 6 blocos autônomos de 5s),
    salva localmente e agenda o processamento assíncrono.
    Responde com 200 OK imediato (< 200ms) para liberar a câmera do celular.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Nenhum arquivo de vídeo foi enviado.")

    job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    job_dir = TEMP_BASE_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Recebendo {len(files)} arquivos para o job {job_id} | Evento: {evento} | Duração: {duracao}")

    saved_chunk_paths: List[Path] = []
    for idx, upload_file in enumerate(files):
        # Preserva extensão do arquivo recebido (geralmente .webm ou .mp4)
        ext = Path(upload_file.filename or "chunk.webm").suffix or ".webm"
        chunk_file = job_dir / f"chunk_{idx:03d}{ext}"
        
        with open(chunk_file, "wb") as f:
            content = await upload_file.read()
            f.write(content)
        
        saved_chunk_paths.append(chunk_file)

    # Agenda processamento e envio em segundo plano
    background_tasks.add_task(
        process_and_dispatch_replay,
        job_dir=job_dir,
        chunk_paths=saved_chunk_paths,
        duracao=duracao,
        evento=evento,
        webhook_url=webhook_url
    )

    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "message": "Replay recebido e enfileirado para processamento.",
            "job_id": job_id,
            "chunks_count": len(saved_chunk_paths),
            "duracao": duracao,
            "evento": evento
        }
    )

# Monta o frontend estático se o diretório existir
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    logger.warning(f"Diretório do frontend não encontrado em: {FRONTEND_DIR}")
