"""
app.py — Servidor FastAPI para o sistema Replay Mobile.

Roda no Android via Termux (ou Windows para desenvolvimento).
Serve a interface web e recebe chunks de vídeo via WebSocket.

Uso:
    python app.py

Acesse no browser:
    https://localhost:8443  (aceitar certificado self-signed)
    https://<IP-DO-CELULAR>:8443  (de outro dispositivo na rede)
"""

import os
import sys
import ssl
import json
import time
import asyncio
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager

# Forca UTF-8 no stdout do Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from replay_buffer import ReplayBuffer, _log, GREEN, YELLOW, RED, CYAN, BOLD, RESET

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURAÇÕES (persistidas em settings.json)
# ─────────────────────────────────────────────────────────────────────────────
SETTINGS_FILE = "settings.json"
DEFAULT_SETTINGS = {
    "replay_seconds":    30,
    "n8n_webhook_url":   "https://www.n8n.martinsautomation.com.br/webhook/107b32fc-af34-4ea3-8063-5eded29ebc2c",
    "camera_mode":       "native",    # "native" | "external"
    "external_cam_url":  "",          # URL MJPEG/RTSP da câmera externa
    "gesture_enabled":   False,       # Detecção YOLO (pesado no mobile)
    "gesture_hold_secs": 5.0,
    "server_port":       8443,
    "output_dir":        "replays",
    "debounce_secs":     2.0,
}

def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    if Path(SETTINGS_FILE).exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            s.update(saved)
        except Exception:
            pass

    # Sobrescrita via Variáveis de Ambiente (Easypanel / Docker)
    if os.environ.get("N8N_WEBHOOK_URL"):
        s["n8n_webhook_url"] = os.environ["N8N_WEBHOOK_URL"].strip()
    if os.environ.get("PORT"):
        try:
            s["server_port"] = int(os.environ["PORT"])
        except ValueError:
            pass
    if os.environ.get("REPLAY_SECONDS"):
        try:
            s["replay_seconds"] = int(os.environ["REPLAY_SECONDS"])
        except ValueError:
            pass

    return s

def save_settings(s: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)

settings = load_settings()

# ─────────────────────────────────────────────────────────────────────────────
#  BUFFER GLOBAL
# ─────────────────────────────────────────────────────────────────────────────
buf = ReplayBuffer(
    max_seconds    = settings["replay_seconds"],
    output_dir     = settings["output_dir"],
    n8n_webhook_url= settings["n8n_webhook_url"],
    debounce_secs  = settings["debounce_secs"],
)

# ─────────────────────────────────────────────────────────────────────────────
#  CÂMERA EXTERNA (RTSP/MJPEG via OpenCV — opcional)
# ─────────────────────────────────────────────────────────────────────────────
_ext_cam_thread = None
_ext_cam_running = False
_ext_cam_frame_bytes = None   # último frame JPEG para streaming via SSE
_ext_cam_lock = threading.Lock()

def _external_cam_loop(url: str):
    """
    Thread que lê frames da câmera externa (Iriun/RTSP/MJPEG) e:
    - Mantém o último frame disponível para streaming MJPEG ao browser
    - Alimenta o buffer com frames codificados como chunks WebM (via ffmpeg pipe)
    """
    global _ext_cam_frame_bytes, _ext_cam_running
    try:
        import cv2
    except ImportError:
        _log("opencv-python não instalado. Câmera externa indisponível.", RED)
        _ext_cam_running = False
        return

    _log(f"[CÂMERA EXT] Conectando: {url}", CYAN)
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        _log(f"[CÂMERA EXT] Falha ao abrir {url}", RED)
        _ext_cam_running = False
        return

    _log("[CÂMERA EXT] Conectada!", GREEN)

    while _ext_cam_running:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        # Codifica frame como JPEG para preview
        _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        with _ext_cam_lock:
            _ext_cam_frame_bytes = jpg.tobytes()

        # Alimenta o buffer com chunk (1 frame como WebM mínimo)
        # Para câmera externa, usamos frames individuais como "chunks"
        # O buffer aceita qualquer bytes — no save, o ffmpeg lida com isso
        buf.push_chunk(jpg.tobytes())

    cap.release()
    _log("[CÂMERA EXT] Thread encerrada.", YELLOW)

def start_external_cam(url: str):
    global _ext_cam_thread, _ext_cam_running
    stop_external_cam()
    _ext_cam_running = True
    _ext_cam_thread = threading.Thread(target=_external_cam_loop, args=(url,), daemon=True)
    _ext_cam_thread.start()

def stop_external_cam():
    global _ext_cam_running, _ext_cam_thread, _ext_cam_frame_bytes
    _ext_cam_running = False
    if _ext_cam_thread and _ext_cam_thread.is_alive():
        _ext_cam_thread.join(timeout=3)
    _ext_cam_thread = None
    with _ext_cam_lock:
        _ext_cam_frame_bytes = None

# ─────────────────────────────────────────────────────────────────────────────
#  FASTAPI APP
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app_):
    """Startup e shutdown do servidor."""
    # Startup
    if settings["camera_mode"] == "external" and settings["external_cam_url"]:
        start_external_cam(settings["external_cam_url"])
    yield
    # Shutdown
    stop_external_cam()

from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Replay Mobile", version="1.0.0", lifespan=lifespan)

# Serve arquivos estáticos (index.html, logo.png, etc)
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve a interface principal."""
    html_path = static_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Arquivo index.html não encontrado!</h1>", status_code=404)

# ─────────────────────────────────────────────────────────────────────────────
#  WebSocket — câmera nativa do browser
# ─────────────────────────────────────────────────────────────────────────────
_active_ws_clients = set()

@app.websocket("/ws/camera")
async def camera_ws(ws: WebSocket):
    """
    Recebe chunks WebM do MediaRecorder do browser.
    Primeiro chunk = init segment (cabeçalho WebM).
    Chunks seguintes = segmentos de vídeo de ~1s.
    """
    await ws.accept()
    _active_ws_clients.add(ws)
    is_first = True
    _log("[WS] Cliente conectado — recebendo stream da câmera.", GREEN)

    try:
        async for data in ws.iter_bytes():
            if is_first:
                buf.set_init_chunk(data)
                buf.push_chunk(data)
                is_first = False
            else:
                buf.push_chunk(data)
    except WebSocketDisconnect:
        _log("[WS] Cliente desconectado.", YELLOW)
    finally:
        _active_ws_clients.discard(ws)

# ─────────────────────────────────────────────────────────────────────────────
#  API REST
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/save-replay")
async def save_replay(request: Request):
    """Dispara o salvamento do replay (botão na UI)."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    source = body.get("source", "botão")
    started = buf.trigger_save(source=source)
    return JSONResponse({
        "status": "saving" if started else "ignored",
        "save_count": buf.save_count,
    })

@app.get("/api/status")
async def api_status():
    """Status atual do buffer para a UI atualizar em tempo real."""
    return JSONResponse({
        "buffer_secs":  round(buf.current_seconds, 1),
        "max_secs":     buf.max_seconds,
        "chunk_count":  buf.chunk_count,
        "saving":       buf.saving,
        "save_count":   buf.save_count,
        "camera_mode":  settings["camera_mode"],
        "ext_cam_ok":   _ext_cam_running,
        "active_cams":  len(_active_ws_clients),
    })

@app.get("/api/settings")
async def get_settings():
    return JSONResponse(settings)

@app.post("/api/settings")
async def update_settings(request: Request):
    global buf, settings
    body = await request.json()

    # Atualiza settings
    old_seconds = settings["replay_seconds"]
    settings.update(body)
    save_settings(settings)

    # Recria buffer se max_seconds mudou
    if settings["replay_seconds"] != old_seconds:
        buf.max_seconds = settings["replay_seconds"]

    buf.n8n_webhook_url = settings["n8n_webhook_url"]
    buf.debounce_secs   = settings["debounce_secs"]

    # Gerencia câmera externa
    if settings["camera_mode"] == "external" and settings["external_cam_url"]:
        start_external_cam(settings["external_cam_url"])
    else:
        stop_external_cam()

    return JSONResponse({"status": "ok", "settings": settings})

@app.get("/api/video/stream")
async def mjpeg_stream():
    """
    Stream MJPEG da câmera externa para preview no browser.
    Apenas disponível quando camera_mode == 'external'.
    """
    from fastapi.responses import StreamingResponse

    async def gen():
        while _ext_cam_running:
            with _ext_cam_lock:
                frame = _ext_cam_frame_bytes
            if frame:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )
            await asyncio.sleep(0.04)  # ~25fps

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace;boundary=frame")

@app.get("/api/replays")
async def list_replays():
    """Lista os últimos replays salvos."""
    out_dir = Path(settings["output_dir"])
    if not out_dir.exists():
        return JSONResponse({"replays": []})
    files = sorted(
        [f for f in out_dir.iterdir() if f.suffix in (".mp4", ".webm") and "raw" not in f.name],
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )[:10]
    return JSONResponse({
        "replays": [
            {
                "name": f.name,
                "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
                "ts": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
            for f in files
        ]
    })

# ─────────────────────────────────────────────────────────────────────────────
#  GERAÇÃO DE CERTIFICADO SSL SELF-SIGNED
# ─────────────────────────────────────────────────────────────────────────────
def ensure_ssl_cert(local_ip: str = "127.0.0.1") -> bool:
    """Gera certificado self-signed se não existir."""
    if Path("cert.pem").exists() and Path("key.pem").exists():
        return True
    _log("Gerando certificado SSL self-signed...", YELLOW)

    # 1. Tenta gerar via biblioteca python 'cryptography' (nativo, sem depender de openssl CLI)
    try:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "replay-local"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Replay Mobile"),
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
        ])

        alt_names = [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]
        if local_ip and local_ip != "127.0.0.1":
            try:
                alt_names.append(x509.IPAddress(ipaddress.IPv4Address(local_ip)))
            except Exception:
                pass

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
            .sign(key, hashes.SHA256())
        )

        with open("key.pem", "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))

        with open("cert.pem", "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        _log(f"Certificado SSL gerado com sucesso (cert.pem / key.pem com IP {local_ip})", GREEN)
        return True
    except Exception as e:
        _log(f"Geração via cryptography falhou ({e}), tentando openssl CLI...", YELLOW)

    # 2. Fallback para openssl CLI
    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", "key.pem", "-out", "cert.pem",
            "-days", "365", "-nodes",
            "-subj", "/CN=replay-local/O=Replay/C=BR"
        ], check=True, capture_output=True)
        _log("Certificado SSL gerado via openssl CLI (cert.pem / key.pem)", GREEN)
        return True
    except FileNotFoundError:
        _log("openssl não encontrado. Rodando em HTTP (câmera bloqueada no mobile sem HTTPS).", RED)
        return False
    except subprocess.CalledProcessError as e:
        _log(f"Erro ao gerar certificado via openssl: {e}", RED)
        return False

# ─────────────────────────────────────────────────────────────────────────────
#  STARTUP
# ─────────────────────────────────────────────────────────────────────────────
# Startup/shutdown gerenciados pelo lifespan acima

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    # Suporta variável de ambiente PORT (padrão em deploys de nuvem / Easypanel)
    port_env = os.environ.get("PORT")
    port = int(port_env) if port_env else int(settings.get("server_port", 8443))

    # Detecta se está em container Docker ou atrás de reverse proxy
    is_docker = os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER") == "1"
    ssl_env = os.environ.get("USE_SSL", "").lower()

    # Em deploys de nuvem / Easypanel, o Traefik/Reverse Proxy gerencia o SSL (Let's Encrypt)
    # externamente, então o container escuta em HTTP puro internamente.
    if ssl_env in ("false", "0", "no") or (is_docker and ssl_env != "true") or port in (80, 8080, 8000, 3000):
        has_ssl = False
    else:
        # Detecta IP local para incluir no certificado self-signed de desenvolvimento local
        import socket
        local_ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        has_ssl = ensure_ssl_cert(local_ip)

    proto = "https" if has_ssl else "http"

    print(
        f"\n{CYAN}{BOLD}=======================================================\n"
        f"  REPLAY WEB - Servidor FastAPI ({'Nuvem/Easypanel' if not has_ssl and port != 8443 else 'Local'})\n"
        f"======================================================={RESET}\n\n"
        f"  {GREEN}Servidor ativo na porta {port} ({proto.upper()}){RESET}\n"
        f"  Buffer  : {settings['replay_seconds']}s\n"
        f"  Output  : {os.path.abspath(settings['output_dir'])}/\n"
        f"  N8N     : {'Configurado' if settings['n8n_webhook_url'] else 'Nao configurado'}\n"
    )

    if has_ssl:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=port,
            ssl_certfile="cert.pem",
            ssl_keyfile="key.pem",
            log_level="warning"
        )
    else:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
