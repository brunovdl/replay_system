import os
import re
import time
import atexit
import socket
import logging
import threading
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("replay.tunnel")

# Diretório base do projeto
BASE_DIR = Path(__file__).resolve().parent.parent.parent

_tunnel_process: Optional[subprocess.Popen] = None
_tunnel_url: Optional[str] = None
_tunnel_lock = threading.Lock()
_tunnel_starting = False

def get_local_ip() -> str:
    """Retorna o IP local na rede Wi-Fi/LAN"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def _find_cloudflared() -> Optional[str]:
    """Localiza o binário do cloudflared no projeto ou no PATH"""
    local_binary = BASE_DIR / "cloudflared.exe"
    if local_binary.exists():
        return str(local_binary)
    import shutil
    sys_binary = shutil.which("cloudflared")
    if sys_binary:
        return sys_binary
    return None

def _read_tunnel_output(proc: subprocess.Popen):
    global _tunnel_url
    try:
        for line in iter(proc.stdout.readline, ''):
            if not line:
                break
            # Procura por URLs do trycloudflare.com ou localtunnel
            cf_match = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', line)
            if cf_match:
                url = cf_match.group(1)
                _tunnel_url = url
                logger.info(f"==> TÚNEL CLOUDFLARE HTTPS ATIVO: {url} <==")
                print(f"\n=======================================================", flush=True)
                print(f" [OK] TÚNEL HTTPS SEGURO PARA CELULAR (ANDROID / IPHONE):", flush=True)
                print(f"   -> {url}", flush=True)
                print(f"=======================================================\n", flush=True)
                
            lt_match = re.search(r'your url is:\s*(https://[a-zA-Z0-9-]+\.loca\.lt)', line)
            if lt_match:
                url = lt_match.group(1)
                _tunnel_url = url
                logger.info(f"==> TÚNEL LOCALTUNNEL HTTPS ATIVO: {url} <==")
    except Exception as e:
        logger.warning(f"Erro lendo output do túnel: {e}")

def start_tunnel(port: int = 8000, wait_for_url: bool = True, timeout: int = 15) -> Optional[str]:
    """Inicia o túnel HTTPS em segundo plano"""
    global _tunnel_process, _tunnel_url, _tunnel_starting

    with _tunnel_lock:
        if _tunnel_process and _tunnel_process.poll() is None:
            # Já está rodando
            return _tunnel_url

        cloudflared_bin = _find_cloudflared()
        if cloudflared_bin:
            cmd = [cloudflared_bin, "tunnel", "--url", f"http://127.0.0.1:{port}"]
            logger.info(f"Iniciando túnel Cloudflare via {cloudflared_bin}...")
        else:
            # Fallback para localtunnel via npx
            cmd = ["npx", "--yes", "localtunnel", "--port", str(port)]
            logger.info("cloudflared não encontrado. Iniciando fallback via localtunnel...")

        try:
            # CREATE_NO_WINDOW no Windows evita abrir janelas indesejadas
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            _tunnel_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags
            )
            _tunnel_url = None
            _tunnel_starting = True

            # Inicia thread para ler stdout/stderr continuamente
            t = threading.Thread(target=_read_tunnel_output, args=(_tunnel_process,), daemon=True)
            t.start()

        except Exception as e:
            logger.error(f"Falha ao iniciar túnel: {e}")
            _tunnel_starting = False
            return None

    if wait_for_url:
        start_t = time.time()
        while time.time() - start_t < timeout:
            if _tunnel_url:
                break
            time.sleep(0.5)

    _tunnel_starting = False
    return _tunnel_url

def stop_tunnel():
    """Encerra o processo do túnel com segurança"""
    global _tunnel_process, _tunnel_url
    with _tunnel_lock:
        if _tunnel_process and _tunnel_process.poll() is None:
            logger.info("Encerrando processo do túnel HTTPS...")
            try:
                _tunnel_process.terminate()
                _tunnel_process.wait(timeout=3)
            except Exception:
                try:
                    _tunnel_process.kill()
                except Exception:
                    pass
            _tunnel_process = None
            _tunnel_url = None

atexit.register(stop_tunnel)

def get_tunnel_info() -> Dict[str, Any]:
    """Retorna o status atual do túnel e IPs de acesso"""
    local_ip = get_local_ip()
    is_running = _tunnel_process is not None and _tunnel_process.poll() is None

    return {
        "running": is_running,
        "url": _tunnel_url,
        "local_ip": local_ip,
        "local_url": f"http://{local_ip}:8000",
        "localhost_url": "http://localhost:8000"
    }
