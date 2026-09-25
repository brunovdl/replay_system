import os
import logging
from pathlib import Path
import httpx

logger = logging.getLogger("replay.n8n")

DEFAULT_WEBHOOK_URL = os.getenv(
    "WEBHOOK_URL",
    "https://geral.n8n.p2me0s.easypanel.host/webhook/replay"
)

async def dispatch_to_n8n(
    video_path: Path,
    duracao: str = "30s",
    evento: str = "Gatilho de Pose (Braço Erguido)",
    webhook_url: str = None
) -> dict:
    """
    Envia o arquivo MP4 convertido e os metadados para o webhook do n8n.
    O nó 'Monta Payload' do n8n espera o binário no form-data e os campos
    'duracao' e 'evento' para envio via Evolution API.
    """
    url = webhook_url or os.getenv("WEBHOOK_URL", DEFAULT_WEBHOOK_URL)
    filename = video_path.name

    logger.info(f"Despachando replay ({filename}, {video_path.stat().st_size} bytes) para n8n: {url}")

    async with httpx.AsyncClient(timeout=60.0) as client:
        with open(video_path, "rb") as video_file:
            files = {
                "file": (filename, video_file, "video/mp4")
            }
            data = {
                "duracao": duracao,
                "evento": evento
            }

            try:
                response = await client.post(url, data=data, files=files)
                logger.info(f"Resposta do webhook n8n: HTTP {response.status_code} - {response.text[:200]}")
                
                if response.status_code >= 400:
                    logger.error(f"Erro ao disparar webhook n8n: HTTP {response.status_code} - {response.text}")
                    return {
                        "success": False,
                        "status_code": response.status_code,
                        "response": response.text
                    }

                return {
                    "success": True,
                    "status_code": response.status_code,
                    "response": response.text
                }

            except Exception as e:
                logger.error(f"Exceção ao comunicar com webhook n8n: {e}")
                return {
                    "success": False,
                    "error": str(e)
                }
