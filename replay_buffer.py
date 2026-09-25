"""
replay_buffer.py — Buffer circular de chunks WebM para o sistema mobile.

Recebe chunks binários do MediaRecorder (browser) via WebSocket.
Mantém os últimos N segundos em memória e, ao disparar, concatena
os chunks em um arquivo WebM → converte para MP4 via ffmpeg → envia ao N8N.
"""

import os
import sys
import time
import threading
import subprocess
import requests
from collections import deque
from datetime import datetime


# ── ANSI colors ───────────────────────────────────────────────────────────────
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def _log(msg, color=RESET):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{color}{BOLD}[{ts}]{RESET} {color}{msg}{RESET}", flush=True)


def get_ffmpeg_bin() -> str:
    """Localiza o binário do ffmpeg (via imageio-ffmpeg ou PATH do sistema)."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass

    import shutil
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg

    return "ffmpeg"


class ReplayBuffer:
    """
    Buffer circular de chunks de vídeo WebM recebidos do browser via WebSocket.

    O browser usa MediaRecorder com timeslice=1000ms, gerando um chunk binário
    a cada segundo. O primeiro chunk contém o cabeçalho de inicialização WebM
    (init segment), obrigatório para que qualquer slice subsequente seja
    decodificável. Esse chunk é armazenado separadamente em self.init_chunk.

    Ao disparar o replay:
      1. Concatena init_chunk + chunks dentro da janela de tempo
      2. Salva como .webm temporário
      3. ffmpeg converte para .mp4 H.264/AAC + faststart (100% compatível com WhatsApp)
      4. Envia via POST multipart ao N8N Webhook
      5. Remove arquivo temporário webm
    """

    def __init__(self, max_seconds: int = 30, output_dir: str = "replays",
                 n8n_webhook_url: str = "", debounce_secs: float = 2.0):
        self.max_seconds     = max_seconds
        self.output_dir      = output_dir
        self.n8n_webhook_url = n8n_webhook_url
        self.debounce_secs   = debounce_secs

        self._lock           = threading.Lock()
        self._chunks: deque  = deque()   # (bytes, float timestamp)
        self.init_chunk      = None      # cabeçalho WebM obrigatório

        self.saving          = False
        self.save_count      = 0
        self._last_save_at   = 0.0
        self._save_lock      = threading.Lock()

        self._first_chunk_at = None
        self._last_chunk_at  = None

    @property
    def current_seconds(self):
        with self._lock:
            if not self._chunks:
                return 0.0
            oldest = self._chunks[0][1]
            newest = self._chunks[-1][1]
            return newest - oldest

    @property
    def chunk_count(self):
        with self._lock:
            return len(self._chunks)

    def set_init_chunk(self, data: bytes):
        self.init_chunk = data
        _log("[BUFFER] Init chunk WebM armazenado.", CYAN)

    def push_chunk(self, data: bytes):
        now = time.time()
        with self._lock:
            self._chunks.append((data, now))
            self._last_chunk_at = now
            if self._first_chunk_at is None:
                self._first_chunk_at = now
            # Mantém margem de segurança de max_seconds + 5.0s para evitar descarte prematuro de chunks
            cutoff = now - (self.max_seconds + 5.0)
            while self._chunks and self._chunks[0][1] < cutoff:
                self._chunks.popleft()

    def reset(self):
        with self._lock:
            self._chunks.clear()
            self.init_chunk = None
            self._first_chunk_at = None
            self._last_chunk_at = None
        _log("[BUFFER] Buffer resetado.", YELLOW)

    def trigger_save(self, source: str = "botão") -> bool:
        now = time.time()
        if self.saving:
            _log("Já salvando um replay, aguarde...", YELLOW)
            return False
        if now - self._last_save_at < self.debounce_secs:
            return False
        with self._lock:
            if not self._chunks:
                _log("Buffer vazio — aguarde acumular vídeo.", RED)
                return False
            # Captura a janela inteira de max_seconds dos chunks recentes
            cutoff = now - (self.max_seconds + 1.0)
            valid_chunks = [c for c in self._chunks if c[1] >= cutoff]
            snapshot = valid_chunks if valid_chunks else list(self._chunks)
        self._last_save_at = now
        self.saving        = True
        self.save_count   += 1
        _log(f"Replay #{self.save_count} iniciado! ({source}, {len(snapshot)} chunks, max {self.max_seconds}s)", CYAN)
        t = threading.Thread(target=self._do_save, args=(snapshot, source), daemon=True)
        t.start()
        return True

    def _do_save(self, snapshot: list, source: str):
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
            webm_path = os.path.join(self.output_dir, f"replay_raw_{ts}.webm")
            mp4_path  = os.path.join(self.output_dir, f"replay_{ts}.mp4")

            _log(f"[SAVE] Gravando {len(snapshot)} chunks WebM...", CYAN)
            with open(webm_path, "wb") as f:
                if self.init_chunk:
                    f.write(self.init_chunk)
                for chunk_bytes, _ in snapshot:
                    f.write(chunk_bytes)

            raw_duration = snapshot[-1][1] - snapshot[0][1] if len(snapshot) > 1 else float(self.max_seconds)
            target_duration = min(float(self.max_seconds), max(raw_duration, 1.0))
            _log(f"WebM bruto salvo ({raw_duration:.1f}s) — limitando saída a {target_duration:.1f}s", GREEN)

            ffmpeg_bin = get_ffmpeg_bin()
            _log(f"[FFMPEG] Convertendo via {os.path.basename(ffmpeg_bin)} (720p, max {target_duration:.1f}s, +faststart)...", CYAN)

            # Comando com suporte a resiliência de pacotes:
            # -fflags +genpts+discardcorrupt: descarta frames parciais/incompletos no inicio do stream (elimina o quadriculado)
            # -vf setpts=PTS-STARTPTS,scale=-2:720: reseta linha de tempo e ajusta resolução
            # -t target_duration: garante o tempo configurado (ex: 15s)
            cmd = [
                ffmpeg_bin, "-y",
                "-fflags", "+genpts+discardcorrupt",
                "-err_detect", "ignore_err",
                "-i", webm_path,
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-vf", "setpts=PTS-STARTPTS,scale=-2:720",
                "-t", f"{target_duration:.2f}",
                "-c:v", "libx264",
                "-profile:v", "main",
                "-level", "3.1",
                "-pix_fmt", "yuv420p",
                "-preset", "faster",
                "-crf", "25",
                "-maxrate", "2000k",
                "-bufsize", "3000k",
                "-c:a", "aac",
                "-b:a", "96k",
                "-movflags", "+faststart",
                "-shortest",
                mp4_path
            ]

            conv_ok = False
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                conv_ok = True
                _log(f"✔ [FFMPEG] MP4 otimizado criado ({target_duration:.1f}s): {mp4_path}", GREEN)
                try:
                    os.remove(webm_path)
                except Exception:
                    pass
            except FileNotFoundError:
                _log("✗ [FFMPEG] ffmpeg não encontrado! Instale: pip install imageio-ffmpeg ou pkg install ffmpeg", RED)
            except subprocess.CalledProcessError as e:
                _log(f"✗ [FFMPEG] Erro na conversão para MP4: {e.stderr[:200] if e.stderr else e}", RED)

            if conv_ok and self.n8n_webhook_url:
                self._send_to_n8n(mp4_path, target_duration, source)
            elif not conv_ok:
                _log("✗ [AVISO] Vídeo não enviado ao WhatsApp porque a conversão MP4 H.264 falhou (evitando arquivo corrompido no app).", YELLOW)

        except Exception as e:
            _log(f"Erro ao salvar replay: {e}", RED)
        finally:
            self.saving = False

    def _send_to_n8n(self, filepath: str, duration: float, source: str):
        try:
            _log(f"[N8N] Enviando para webhook...", CYAN)
            with open(filepath, "rb") as f:
                files = {"file": (os.path.basename(filepath), f, "video/mp4")}
                data  = {"duracao": f"{duration:.1f}", "evento": source}
                resp  = requests.post(self.n8n_webhook_url, files=files, data=data, timeout=60)
            if resp.status_code in (200, 201):
                _log(f"[N8N] Enviado com sucesso! (HTTP {resp.status_code})", GREEN)
            else:
                _log(f"[N8N] Erro HTTP {resp.status_code}: {resp.text[:120]}", RED)
        except Exception as e:
            _log(f"[N8N] Falha ao enviar: {e}", RED)
