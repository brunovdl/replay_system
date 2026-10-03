"""
╔══════════════════════════════════════════════════════╗
║         INSTANT REPLAY SYSTEM — IP Camera            ║
║  Pressione [ESPAÇO] uma vez → salva os últimos 30s  ║
╚══════════════════════════════════════════════════════╝
"""

import os
import sys

# Otimiza o backend FFmpeg do OpenCV para ultra-baixa latência em RTSP
# - nobuffer: desabilita buffering de rede
# - discardcorrupt: descarta frames corrompidos ao invés de tentar decodificar (evita erros POC)
# - analyzeduration/probesize baixos: conexão rápida sem analisar todo o stream
# - max_delay baixo: não acumula frames na fila interna do demuxer
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|"
    "rtsp_flags;nobuffer|"
    "fflags;nobuffer+discardcorrupt|"
    "analyzeduration;500000|"
    "probesize;500000|"
    "max_delay;100000|"
    "reorder_queue_size;0"
)

import cv2
import threading
import collections
import queue
import time
import requests
from datetime import datetime
from pynput import keyboard

try:
    from ultralytics import YOLO as _YOLO_CLASS
    YOLO_OK = True
except ImportError:
    YOLO_OK = False

# Forca UTF-8 no stdout do Windows (evita UnicodeEncodeError com caracteres especiais)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ─────────────────────────────────────────────
#  CONFIGURAÇÕES — edite aqui
# ─────────────────────────────────────────────
CAMERA_SOURCE    = 1            # padrao quando rodado direto, sem o launcher
                                # indice = camera local (ordem do DirectShow) | "rtsp://..." = IP cam
                                # O launcher (iniciar.py) mostra um menu e passa --camera
CAMERA_WIDTH     = 1280         # resolucao pedida a camera local (0 = padrao do driver)
CAMERA_HEIGHT    = 720          # 1280x720 = HD (Iriun suporta) | 640x480 = mais leve
BUFFER_JPEG_QUALITY = 85        # qualidade do JPEG no buffer (0-100)
                                # 85 = sem perda visivel, ~150 MB para 30s em 720p
                                # (sem compressao seriam ~2,5 GB)
REPLAY_SECONDS   = 30           # duração do buffer (segundos)
OUTPUT_DIR       = "replays"    # pasta onde os vídeos serão salvos
REPLAYS_KEEP_DAYS = 30          # apaga replays com mais de N dias (0 = nunca)
REPLAYS_MAX_GB   = 10           # passando disso, apaga os mais antigos (0 = sem limite)
DISPLAY_PREVIEW  = True         # mostrar janela de preview (False = headless)
PREVIEW_WIDTH    = 1280         # largura maxima do preview em pixels (altura calculada automaticamente)
                                # Aumente para ver mais detalhes | Diminua se a janela ficar grande demais
HOTKEY           = keyboard.Key.space   # tecla de gatilho
DEBOUNCE_SECS    = 2.0          # ignora re-acionamentos dentro deste intervalo (segundos)

# ─────────────────────────────────────────────
#  INTEGRAÇÃO (Webhook / N8N / WhatsApp)
# ─────────────────────────────────────────────
def _carregar_env(nome=".env"):
    """Le linhas CHAVE=VALOR do .env ao lado do script (fora do git) para os.environ."""
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), nome)
    if not os.path.exists(caminho):
        return
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                chave, valor = linha.split("=", 1)
                os.environ.setdefault(chave.strip(), valor.strip().strip('"'))

_carregar_env()
# URL do webhook do n8n: fica no arquivo .env (copie .env.example), nunca no codigo
N8N_WEBHOOK_URL  = os.environ.get("N8N_WEBHOOK_URL", "")

# ─────────────────────────────────────────────
#  QUALIDADE DO STREAM (reduz carga de CPU)
# ─────────────────────────────────────────────
STREAM_WIDTH     = 1280         # Resolução horizontal máxima de captura (0 = original da câmera)
                                # 1280 = 720p (recomendado para CPU sem GPU)
                                # 1920 = 1080p (mais pesado)
                                # 960  = ainda mais leve, bom para replay de quadra
AUTO_FLUSH_SECS  = 60           # Reconexão preventiva a cada N segundos para evitar acúmulo de latência
                                # 0 = desativado | 60 = recomendado para sessões longas
MAX_LATENCY_MS   = 3000         # Latência máxima tolerada (ms) antes de forçar reconexão
                                # Evita que o stream fique "atrasado" progressivamente
PREVIEW_SKIP     = False        # True = pula frames intermediários para manter preview em tempo real
                                # Garante que o preview SEMPRE mostra o frame mais recente
                                # mesmo que o rendering demore mais que o intervalo entre frames

# ─────────────────────────────────────────────
#  GATILHO POR GESTO (YOLO POSE — braço levantado)
# ─────────────────────────────────────────────
GESTURE_ENABLED        = True    # True = ativa deteccao de gesto | False = desativa
GESTURE_DEBOUNCE       = 5.0     # cooldown (s) apos um disparo por gesto antes de aceitar outro
GESTURE_HOLD_SECS      = 4.0     # segundos com braço levantado para disparar replay
                                 # Evita falsos positivos durante movimentos normais do volei
GESTURE_GRACE_SECS     = 1.0     # tolerancia (s) a falhas momentaneas de deteccao durante o hold
                                 # O YOLO "perde" o pulso por alguns frames (blur, oclusao);
                                 # o hold so eh cancelado se o braço sumir por mais que isso
GESTURE_KP_CONF        = 0.35    # confianca minima de cada ponto do corpo (pulso, ombro, cabeca)
GESTURE_SOUND          = True    # bipe no PC ao iniciar o hold e ao disparar o replay
                                 # (feedback para quem esta longe da tela)
YOLO_MODEL             = "yolov8n-pose.pt"  # modelo de pose ultraleve ~6MB, baixado automaticamente
YOLO_CONFIDENCE        = 0.35    # confianca minima para detectar a pessoa (jogadores distantes tem conf baixa)
YOLO_DEBUG             = False   # True = desenha esqueleto dos jogadores no preview (modo debug)

# ─────────────────────────────────────────────
#  CONFIGURAÇÕES DE PERFORMANCE
# ─────────────────────────────────────────────
YOLO_INPUT_SIZE        = 480     # tamanho de entrada do YOLO (multiplo de 32)
                                 # 320 = rapido, mas jogadores distantes ficam pequenos demais
                                 # 480 = equilibrio | 640 = mais preciso e mais pesado
YOLO_FPS               = 5       # inferencias por segundo — para um hold de 4s, 5/s sobra
                                 # Limitar aqui eh o que mais reduz o uso de CPU
YOLO_THREADS           = 4       # threads de CPU do PyTorch (0 = padrao, usa todos os nucleos)
YOLO_DEVICE            = "cpu"   # "cpu" fixo (sem GPU NVIDIA disponível)
YOLO_HALF              = False   # FP16 apenas para CUDA; manter False na CPU

# ─────────────────────────────────────────────
#  CORES para o terminal (ANSI)
# ─────────────────────────────────────────────
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def log(msg, color=RESET):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{color}{BOLD}[{ts}]{RESET} {color}{msg}{RESET}", flush=True)


def obter_executavel_ffmpeg():
    """Localiza o executavel do FFmpeg (via imageio_ffmpeg, PATH ou executavel local)."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass

    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        return exe

    return "ffmpeg"


def limpar_replays(mostrar_uso=False):
    """Aplica REPLAYS_KEEP_DAYS e REPLAYS_MAX_GB na pasta de replays. Retorna (qtd, bytes) restantes."""
    if not os.path.isdir(OUTPUT_DIR):
        return 0, 0
    videos = []
    for nome in os.listdir(OUTPUT_DIR):
        caminho = os.path.join(OUTPUT_DIR, nome)
        if nome.startswith("replay") and nome.endswith(".mp4") and os.path.isfile(caminho):
            st = os.stat(caminho)
            videos.append((st.st_mtime, st.st_size, caminho))
    videos.sort()   # mais antigo primeiro

    agora = time.time()
    total = sum(v[1] for v in videos)
    restantes = []
    for i, (criado, tamanho, caminho) in enumerate(videos):
        antigo  = REPLAYS_KEEP_DAYS > 0 and criado < agora - REPLAYS_KEEP_DAYS * 86400
        # Sempre mantem os 3 mais novos (o ultimo pode estar sendo enviado ao n8n)
        excesso = REPLAYS_MAX_GB > 0 and total > REPLAYS_MAX_GB * 1024 ** 3 and i < len(videos) - 3
        if antigo or excesso:
            try:
                os.remove(caminho)
                total -= tamanho
                log(f"🗑  Replay apagado ({'mais de ' + str(REPLAYS_KEEP_DAYS) + ' dias' if antigo else 'limite de espaco'}): {os.path.basename(caminho)}", YELLOW)
                continue
            except OSError:
                pass
        restantes.append(tamanho)
    if mostrar_uso:
        regras = []
        if REPLAYS_KEEP_DAYS > 0:
            regras.append(f"apaga apos {REPLAYS_KEEP_DAYS} dias")
        if REPLAYS_MAX_GB > 0:
            regras.append(f"limite {REPLAYS_MAX_GB:g} GB")
        print(f"  Replays  : {len(restantes)} arquivo(s), {total / 1024 ** 2:.0f} MB"
              + (f" ({', '.join(regras)})" if regras else ""))
    return len(restantes), total



# ─────────────────────────────────────────────
#  REPLAY BUFFER (buffer circular thread-safe)
# ─────────────────────────────────────────────
class ReplayBuffer:
    def __init__(self):
        self.lock           = threading.Lock()
        self.frames         = collections.deque()   # (jpeg_bytes, timestamp)
        self.fps            = 25.0
        self.max_secs       = REPLAY_SECONDS
        self.save_count     = 0
        # Replays em processamento (gravacao/conversao/envio). Novos disparos
        # nunca sao recusados: o snapshot eh tirado na hora e entra na fila.
        self._pending       = 0
        self._pending_lock  = threading.Lock()
        self._process_lock  = threading.Lock()   # serializa ffmpeg/envio (1 por vez)
        self._jpeg_params   = [cv2.IMWRITE_JPEG_QUALITY, BUFFER_JPEG_QUALITY]

        # Debounce: controla disparos duplicados do teclado
        self._key_held      = False   # True enquanto a tecla estiver pressionada
        self._last_save_at  = 0.0    # timestamp do último save iniciado

    @property
    def saving(self):
        return self._pending > 0

    def set_fps(self, fps):
        self.fps = fps

    def push(self, frame):
        """Armazena o frame no buffer circular, comprimido em JPEG.

        Um frame 1280x720 cru ocupa ~2,8 MB (30s @ 30fps = ~2,5 GB de RAM);
        em JPEG ~120-200 KB (~150 MB no total). A compressao leva ~5 ms.
        imencode gera um buffer novo, entao nao precisa de frame.copy()
        mesmo quando o OpenCV reutiliza o buffer da webcam.
        """
        now = time.time()
        ok, jpeg = cv2.imencode(".jpg", frame, self._jpeg_params)
        if not ok:
            return
        with self.lock:
            self.frames.append((jpeg, now))
            cutoff = now - self.max_secs
            while self.frames and self.frames[0][1] < cutoff:
                self.frames.popleft()

    def snapshot(self):
        """Retorna snapshot rápido como tuple (mais rápido que list())."""
        with self.lock:
            return tuple(self.frames)

    # ── ponto de entrada único para salvar ──
    def trigger_save(self, source="teclado"):
        """
        Chamado por qualquer fonte (teclado global, janela OpenCV).
        Garante disparo único mesmo se a tecla for mantida pressionada.
        """
        now = time.time()

        # Bloqueio de tecla pressionada (key-repeat do SO)
        if self._key_held:
            return

        # Debounce temporal: evita duplo disparo dentro de DEBOUNCE_SECS
        if now - self._last_save_at < DEBOUNCE_SECS:
            return

        self._key_held     = True
        self._last_save_at = now
        self._do_save(source)

    def release_key(self):
        """Libera o bloqueio quando a tecla for solta."""
        self._key_held = False

    def _do_save(self, source):
        snap = self.snapshot()
        if not snap:
            log("✗  Buffer vazio, aguarde acumular frames.", RED)
            return

        with self._pending_lock:
            self.save_count += 1
            self._pending   += 1
            numero = self.save_count
            fila   = self._pending - 1
        # Timestamp do disparo (nao do processamento, que pode estar na fila)
        base_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Dois disparos no mesmo segundo (teclado + gesto) nao podem sobrescrever um ao outro
        ts = f"{base_ts}_{numero}" if base_ts == getattr(self, "_last_ts", None) else base_ts
        self._last_ts = base_ts

        extra = f" (na fila, {fila} antes)" if fila else ""
        log(f"🎬  [{source}] Replay #{numero} iniciado!{extra}", CYAN)

        def _write():
            # Um replay por vez: evita varios ffmpeg simultaneos travando a CPU
            self._process_lock.acquire()
            try:
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                fps      = max(1, round(self.fps))
                duration = snap[-1][1] - snap[0][1]

                # --- FFMPEG (H.264 + AAC + yuv420p + faststart para WhatsApp) ---
                # Os JPEGs do buffer vao direto para o stdin do ffmpeg (image2pipe):
                # uma unica passada, sem decodificar em Python e sem arquivo bruto.
                h264_filename = os.path.join(OUTPUT_DIR, f"replay_zap_{ts}.mp4")
                arquivo_final = h264_filename
                try:
                    import subprocess
                    import tempfile
                    ffmpeg_bin = obter_executavel_ffmpeg()
                    log(f"[FFMPEG] Gerando H.264/AAC (WhatsApp compativel com faststart)...", CYAN)

                    # -f image2pipe -c:v mjpeg: le a sequencia de JPEGs pelo stdin
                    # -movflags +faststart: Move o 'moov atom' para o início do arquivo (essencial para o WhatsApp carregar e reproduzir imediatamente)
                    # -vf scale=...: Garante dimensões pares (exigência do H.264 yuv420p) e converte o
                    #   yuvj420p "full range" do JPEG para yuv420p padrão (players do iPhone/WhatsApp)
                    # -f lavfi -i anullsrc: Gera faixa de áudio AAC silenciosa (evita falha de reprodução no WhatsApp)
                    cmd = [
                        ffmpeg_bin, "-y",
                        "-f", "image2pipe", "-c:v", "mjpeg", "-framerate", str(fps), "-i", "-",
                        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                        "-c:v", "libx264",
                        "-preset", "fast",
                        "-crf", "23",
                        "-profile:v", "main",
                        "-pix_fmt", "yuv420p",
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2:out_range=tv,format=yuv420p",
                        "-movflags", "+faststart",
                        "-c:a", "aac",
                        "-b:a", "128k",
                        "-shortest",
                        h264_filename
                    ]
                    
                    # stderr vai para arquivo temporario: um PIPE nao lido enche
                    # e trava o ffmpeg enquanto ainda estamos escrevendo no stdin
                    with tempfile.TemporaryFile() as err:
                        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                                stdout=subprocess.DEVNULL, stderr=err)
                        total = len(snap)
                        try:
                            for i, (jpeg, _) in enumerate(snap):
                                proc.stdin.write(jpeg.tobytes())
                                pct = int((i + 1) / total * 30)
                                bar = "█" * pct + "░" * (30 - pct)
                                sys.stdout.write(f"\r  [{bar}] {i+1}/{total} frames")
                                sys.stdout.flush()
                        except (BrokenPipeError, OSError):
                            pass   # ffmpeg morreu; o erro aparece no returncode abaixo
                        finally:
                            proc.stdin.close()
                        print()
                        proc.wait()
                        if proc.returncode != 0:
                            err.seek(0)
                            msg = err.read().decode("utf-8", errors="replace")[-300:]
                            raise RuntimeError(f"FFmpeg ({proc.returncode}): {msg or 'erro desconhecido'}")

                    log(f"✔  [FFMPEG] Replay salvo! {duration:.1f}s — {h264_filename}", GREEN)
                except Exception as ef:
                    if isinstance(ef, FileNotFoundError):
                        log(f"✗  [AVISO] FFmpeg não encontrado! O vídeo não roda nativamente no WhatsApp.", YELLOW)
                        log(f"          Instale a dependência: pip install imageio-ffmpeg", YELLOW)
                    else:
                        log(f"✗  [FFMPEG] Erro na conversão: {ef}", RED)
                    # Fallback: grava com o OpenCV (mp4v) para nao perder o lance
                    arquivo_final = os.path.join(OUTPUT_DIR, f"replay_{ts}.mp4")
                    self._gravar_opencv(snap, fps, arquivo_final)
                    log(f"✔  Bruto salvo (mp4v)! {duration:.1f}s — {arquivo_final}", GREEN)

                if N8N_WEBHOOK_URL:
                    try:
                        log(f"[N8N] Enviando video para o Webhook...", CYAN)
                        with open(arquivo_final, 'rb') as f:
                            files = {'file': (os.path.basename(arquivo_final), f, 'video/mp4')}
                            data  = {'duracao': f"{duration:.1f}", 'evento': source}
                            resp  = requests.post(N8N_WEBHOOK_URL, files=files, data=data, timeout=60)
                            
                        if resp.status_code in (200, 201):
                            log(f"✔  [N8N] Video enviado com sucesso! (Status {resp.status_code})", GREEN)
                        else:
                            log(f"✗  [N8N] Retornou erro HTTP {resp.status_code}: {resp.text[:100]}", RED)
                    except Exception as err_net:
                        log(f"✗  [N8N] Falha de conexao ao enviar webhook: {err_net}", RED)

            except Exception as e:
                log(f"✗  Erro ao salvar: {e}", RED)
            finally:
                try:
                    limpar_replays()
                except Exception as e_limpeza:
                    log(f"✗  Erro ao limpar replays antigos: {e_limpeza}", RED)
                self._process_lock.release()
                with self._pending_lock:
                    self._pending -= 1

        threading.Thread(target=_write, daemon=True).start()

    @staticmethod
    def _gravar_opencv(snap, fps, filename):
        """Fallback sem ffmpeg: decodifica os JPEGs e grava com cv2.VideoWriter."""
        primeiro = cv2.imdecode(snap[0][0], cv2.IMREAD_COLOR)
        h, w = primeiro.shape[:2]
        writer = cv2.VideoWriter(filename, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        for jpeg, _ in snap:
            writer.write(cv2.imdecode(jpeg, cv2.IMREAD_COLOR))
        writer.release()


# ─────────────────────────────────────────────
#  KEYBOARD LISTENER global (pynput)
# ─────────────────────────────────────────────
def start_keyboard_listener(buf: ReplayBuffer):
    def on_press(key):
        if key == HOTKEY:
            buf.trigger_save("teclado")

    def on_release(key):
        if key == HOTKEY:
            buf.release_key()   # permite próximo acionamento ao soltar

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()
    return listener


# ─────────────────────────────────────────────
#  CAPTURE THREAD
# ─────────────────────────────────────────────
class CaptureThread(threading.Thread):
    def __init__(self, source, buf: ReplayBuffer, gesture_thr = None):
        super().__init__(daemon=True)
        self.source  = source
        self.buf     = buf
        self.gesture_thr = gesture_thr
        self.running = False
        self.cap     = None

        # Controle de debounce para a tecla dentro da janela OpenCV
        self._cv_space_held = False
        self._win_name = "Replay Cam — [ESPACO] para salvar | [Q] Sair"

    @staticmethod
    def _redimensionar_preview(frame, largura_max):
        """
        Redimensiona o frame para o preview mantendo a proporção original.
        O frame original (resolucao completa) eh preservado no buffer para o replay.
        Retorna o frame redimensionado apenas para exibicao.
        """
        h, w = frame.shape[:2]
        if w <= largura_max:
            return frame          # ja cabe, sem alteracao
        escala = largura_max / w
        nova_w = largura_max
        nova_h = int(h * escala)
        return cv2.resize(frame, (nova_w, nova_h), interpolation=cv2.INTER_AREA)

    def _inicializar_captura(self):
        # Se for webcam local (índice ou string numérica)
        if isinstance(self.source, int) or (isinstance(self.source, str) and self.source.isdigit()):
            # DirectShow: abre em ~1s (MSMF leva ~20s) e o indice bate com a
            # lista de nomes mostrada pelo launcher
            cap = cv2.VideoCapture(int(self.source), cv2.CAP_DSHOW)
            if CAMERA_WIDTH > 0 and CAMERA_HEIGHT > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
            return cap
        else:
            # É stream de rede RTSP (usa FFmpeg para latência mínima e buffer 1)
            cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            # Reduz resolução de captura se configurado (diminui carga de decodificação)
            if STREAM_WIDTH > 0:
                orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                if orig_w > STREAM_WIDTH and orig_w > 0:
                    scale = STREAM_WIDTH / orig_w
                    new_h = int(orig_h * scale)
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, STREAM_WIDTH)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, new_h)

            return cap

    def _flush_reconectar(self, motivo=""):
        """Reconexão limpa: destrói o VideoCapture inteiro e cria um novo.

        Isso força o FFmpeg a descartar todo o estado interno do decoder HEVC,
        eliminando referências de POC corrompidas e latência acumulada.
        """
        tag = f" ({motivo})" if motivo else ""
        log(f"🔄  Reconectando stream{tag}...", YELLOW)
        try:
            if self.cap is not None:
                self.cap.release()
                self.cap = None
        except Exception:
            pass
        time.sleep(0.5)   # pausa curta para o go2rtc liberar a sessão RTSP
        self.cap = self._inicializar_captura()
        if self.cap.isOpened():
            # Descarta os primeiros frames (podem ter referências antigas)
            for _ in range(5):
                self.cap.read()
            log("✔  Stream reconectado com sucesso.", GREEN)
        else:
            log("✗  Falha na reconexão, tentando novamente em 2s...", RED)
            time.sleep(2)
            self.cap = self._inicializar_captura()

    def run(self):
        self.cap = self._inicializar_captura()
        if not self.cap.isOpened():
            log(f"✗  Não foi possível abrir câmera: {self.source}", RED)
            return

        fps = self.cap.get(cv2.CAP_PROP_FPS)
        if fps and fps > 1:
            self.buf.set_fps(fps)
            w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            log(f"Camera conectada | {w}x{h} @ {fps:.1f} FPS", GREEN)
        else:
            w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            log(f"Camera conectada | {w}x{h} | FPS nao detectado, usando 25", YELLOW)

        # Cria janela redimensionavel ANTES do loop (evita corte de imagens grandes)
        if DISPLAY_PREVIEW:
            cv2.namedWindow(self._win_name, cv2.WINDOW_NORMAL)
            # Reutiliza w/h já lidos acima para dimensionar a janela inicial
            if w > 0 and h > 0:
                w_win = min(PREVIEW_WIDTH, w)
                h_win = int(h * (w_win / w))
                cv2.resizeWindow(self._win_name, w_win, h_win)

        self.running = True
        frame_times      = collections.deque(maxlen=60)
        last_fps_upd     = time.time()
        last_flush_time  = time.time()    # controle do auto-flush periódico
        consecutive_fails = 0              # contador de falhas consecutivas de leitura

        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                consecutive_fails += 1
                if consecutive_fails >= 3:
                    # 3+ falhas seguidas: reconexão limpa completa
                    self._flush_reconectar("frames perdidos consecutivos")
                    consecutive_fails = 0
                    last_flush_time = time.time()
                else:
                    log("⚠  Frame perdido, aguardando...", YELLOW)
                    time.sleep(0.3)
                continue

            consecutive_fails = 0
            now = time.time()

            # ── Frame skip: drena todos os frames pendentes do buffer FFmpeg ──
            # O cap.read() pode ter vários frames enfileirados internamente.
            # Sem esse dreno, o preview fica "atrasado" porque exibe frames antigos.
            # Todos os frames drenados vão para o buffer de replay (sem perda),
            # mas só o ÚLTIMO é exibido no preview.
            if PREVIEW_SKIP and DISPLAY_PREVIEW:
                drained = 0
                while True:
                    # grab() é ~10x mais rápido que read() (não decodifica)
                    grabbed = self.cap.grab()
                    if not grabbed:
                        break
                    # Decodifica e substitui: agora 'frame' é o mais recente
                    # O frame anterior vai para o buffer antes de ser substituído
                    self.buf.push(frame)
                    frame_times.append(now)
                    ret2, new_frame = self.cap.retrieve()
                    if ret2:
                        frame = new_frame
                        drained += 1
                        now = time.time()
                    else:
                        break
                    # Limite de segurança: não drena mais que 10 frames por ciclo
                    if drained >= 10:
                        break

            frame_times.append(now)

            # ── Auto-flush periódico para evitar acúmulo de latência ──
            # Após AUTO_FLUSH_SECS segundos sem reconexão, força um flush
            # preemptivo. Isso destrói o decoder HEVC antigo e cria um novo,
            # garantindo que referências de POC não se acumulem.
            if AUTO_FLUSH_SECS > 0 and (now - last_flush_time) > AUTO_FLUSH_SECS:
                # Verifica se há sinais de latência antes de forçar reconexão
                if len(frame_times) > 10:
                    actual_fps = len(frame_times) / max(0.01, frame_times[-1] - frame_times[0])
                    expected_fps = self.buf.fps
                    # Se FPS real caiu mais de 30% do esperado, há acúmulo
                    if actual_fps < expected_fps * 0.7:
                        self._flush_reconectar("FPS degradado — flush preventivo")
                        frame_times.clear()
                        last_flush_time = time.time()
                        continue
                last_flush_time = now  # reseta timer mesmo sem reconexão

            # Push do frame mais recente no buffer de replay
            self.buf.push(frame)

            # Passa frame para a thread de gesto (sempre o mais recente)
            if GESTURE_ENABLED and self.gesture_thr:
                self.gesture_thr.post_frame(frame)

            # atualiza FPS real a cada 3s
            if now - last_fps_upd > 3 and len(frame_times) > 5:
                elapsed = frame_times[-1] - frame_times[0]
                if elapsed > 0:
                    self.buf.set_fps(len(frame_times) / elapsed)
                last_fps_upd = now

            if DISPLAY_PREVIEW:
                # Redimensiona apenas para exibição (buffer mantém resolução original)
                display = self._redimensionar_preview(frame, PREVIEW_WIDTH)
                self._draw_overlay(display)
                cv2.imshow(self._win_name, display)
                key = cv2.waitKey(1) & 0xFF

                if key == ord('q'):
                    self.running = False
                    break

                # Espaço na janela OpenCV: dispara só no press, não no hold
                elif key == ord(' '):
                    if not self._cv_space_held:
                        self._cv_space_held = True
                        # Delega ao mesmo trigger_save centralizado
                        self.buf.trigger_save("janela")
                else:
                    # qualquer outra tecla (ou sem tecla) libera o hold
                    if self._cv_space_held:
                        self._cv_space_held = False
                        self.buf.release_key()

        self.cap.release()
        if DISPLAY_PREVIEW:
            cv2.destroyAllWindows()

    def _draw_overlay(self, frame):
        buf_secs = 0
        with self.buf.lock:
            if self.buf.frames:
                buf_secs = self.buf.frames[-1][1] - self.buf.frames[0][1]

        h, w = frame.shape[:2]
        fill = min(buf_secs / REPLAY_SECONDS, 1.0)

        # Barra de buffer no topo — retângulo opaco (sem frame.copy() custoso)
        # Usar ROI slice + addWeighted apenas na faixa de 30px é ~20x mais
        # rápido que copiar o frame inteiro para simular transparência.
        bar_total = w - 20
        bar_fill  = int(fill * bar_total)
        roi_bar = frame[0:30, 0:w].copy()
        cv2.rectangle(roi_bar, (0, 0), (w, 30), (0, 0, 0), -1)
        cv2.addWeighted(roi_bar, 0.55, frame[0:30, 0:w], 0.45, 0, frame[0:30, 0:w])
        cv2.rectangle(frame, (10, 6), (w - 10, 18), (40, 40, 40), -1)

        # Cor da barra: vermelho → amarelo → verde conforme enche
        if fill < 0.5:
            r, g, b = int(255 * (1 - fill * 2)), int(180 * fill * 2), 0
        else:
            r, g, b = 0, 180, int(80 * (fill - 0.5) * 2)
        cv2.rectangle(frame, (10, 6), (10 + bar_fill, 18), (b, g, r), -1)

        yolo_info = ""
        if GESTURE_ENABLED and self.gesture_thr:
            thr = self.gesture_thr
            if YOLO_OK:
                # Leitura sem lock: cache atômico, pior caso = 1 frame desatualizado
                yolo_info = f" (Jogadores: {thr.latest_persons_count})"
            else:
                yolo_info = " (YOLO Erro)"

        label = (f"Buffer: {buf_secs:.1f}/{REPLAY_SECONDS}s  |  "
                 f"FPS: {self.buf.fps:.1f}{yolo_info}  |  "
                 f"[ESPACO] Salvar replay  [Q] Sair")
        cv2.putText(frame, label, (12, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (210, 230, 210), 1, cv2.LINE_AA)

        # Indicador SAVING no canto superior direito
        if self.buf.saving:
            cv2.circle(frame, (w - 18, 44), 7, (0, 0, 220), -1)
            cv2.putText(frame, "SALVANDO...", (w - 115, 48),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 80, 255), 1, cv2.LINE_AA)

        # Desenha caixas de detecção e esqueletos no modo debug
        # Leitura sem lock: cache atômico garante consistência suficiente para o preview
        if GESTURE_ENABLED and self.gesture_thr and YOLO_OK and YOLO_DEBUG:
            thr = self.gesture_thr
            # Snapshot único do cache — sem lock, pior caso = 1 frame desatualizado
            result = thr._result_cache
            skeletons      = result["skeletons"]
            gesture_active = result["gesture"] == "Arm_Raised"
            feedback_active = time.time() < result["feedback_until"]

            for sk in skeletons:
                pts = sk['points']
                raising = sk['raising_arm']
                # Cor: verde se braço levantado (trigger), laranja se repouso
                color = (0, 255, 80) if raising else (255, 120, 0)

                # Converte coordenadas normalizadas para pixel
                pts_px = [(int(pt['x'] * w), int(pt['y'] * h)) for pt in pts]

                # Desenha conexões do esqueleto
                for a, b in _POSE_CONNECTIONS:
                    if pts[a]['conf'] > GESTURE_KP_CONF and pts[b]['conf'] > GESTURE_KP_CONF:
                        cv2.line(frame, pts_px[a], pts_px[b], color, 2)

                # Desenha pontos principais
                pontos_principais = [0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
                for idx in pontos_principais:
                    if pts[idx]['conf'] > GESTURE_KP_CONF:
                        cv2.circle(frame, pts_px[idx], 4, (0, 220, 0), -1)

                # Desenha texto de feedback no preview se braço levantado
                if raising and pts[0]['conf'] > GESTURE_KP_CONF:
                    nx, ny = pts_px[0]
                    cv2.putText(frame, "REPLAY TRIGGER!", (nx - 60, ny - 20),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 80), 2, cv2.LINE_AA)

            # Contador de hold durante o período de espera
            gesture_start = result["gesture_start"]
            if gesture_start > 0.0 and not self._result_cache_replay_fired(result):
                self._draw_hold_counter(frame, gesture_start, w, h)

            if gesture_active and gesture_start == 0.0:
                # Gesto disparou: mostra feedback de REPLAY
                cv2.rectangle(frame, (0, 0), (w, h), (0, 255, 80), 5)
                cv2.putText(frame, ">>> REPLAY! <<<",
                            (int(w * 0.35), int(h * 0.55)),
                            cv2.FONT_HERSHEY_DUPLEX, 1.3, (0, 50, 255), 3, cv2.LINE_AA)

        else:
            if GESTURE_ENABLED and self.gesture_thr:
                # Leitura sem lock — cache atômico
                result = self.gesture_thr._result_cache
                gesture_active = result["gesture"] == "Arm_Raised"
                gesture_start  = result["gesture_start"]
                if gesture_start > 0.0 and not self._result_cache_replay_fired(result):
                    self._draw_hold_counter(frame, gesture_start, w, h)
                if gesture_active and gesture_start == 0.0:
                    # Já disparou (feedback_until ativo)
                    cv2.rectangle(frame, (0, 0), (w, h), (0, 255, 80), 5)
                    cv2.putText(frame, ">>> REPLAY! <<<",
                                (int(w * 0.35), int(h * 0.55)),
                                cv2.FONT_HERSHEY_DUPLEX, 1.3, (0, 50, 255), 3, cv2.LINE_AA)

    @staticmethod
    def _result_cache_replay_fired(result):
        """Retorna True se o replay já foi disparado (gesture_start zerado após disparo)."""
        return result["gesture_start"] == 0.0 and result["feedback_until"] > time.time()

    def _draw_hold_counter(self, frame, gesture_start, w, h):
        """Desenha o contador regressivo de hold na tela de preview.

        Exibe:
        - Barra de progresso laranja/vermelha no centro inferior
        - Texto com segundos restantes
        - Bordas pulsantes para chamar atenção do operador
        """
        agora   = time.time()
        elapsed = agora - gesture_start
        restante = max(0.0, GESTURE_HOLD_SECS - elapsed)
        progress = min(elapsed / GESTURE_HOLD_SECS, 1.0)

        # ── Borda pulsante (alaranjada) indicando hold em andamento ──────
        borda_cor = (0, 140, 255)   # laranja
        cv2.rectangle(frame, (0, 0), (w, h), borda_cor, 4)

        # ── Barra de progresso centralizada na parte inferior ────────────
        bar_w     = int(w * 0.50)
        bar_h     = 18
        bar_x     = (w - bar_w) // 2
        bar_y     = h - 60
        fill_w    = int(bar_w * progress)

        # Fundo escuro semi-transparente atrás da barra — ROI mínimo (sem frame.copy())
        rx1 = max(0, bar_x - 10)
        ry1 = max(0, bar_y - 30)
        rx2 = min(w, bar_x + bar_w + 10)
        ry2 = min(h, bar_y + bar_h + 10)
        roi_bg = frame[ry1:ry2, rx1:rx2].copy()
        cv2.rectangle(roi_bg, (0, 0), (rx2 - rx1, ry2 - ry1), (0, 0, 0), -1)
        cv2.addWeighted(roi_bg, 0.55, frame[ry1:ry2, rx1:rx2], 0.45, 0, frame[ry1:ry2, rx1:rx2])

        # Trilho da barra
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h),
                      (50, 50, 50), -1)

        # Preenchimento da barra (verde conforme enche)
        if progress < 0.6:
            bar_color = (0, 140, 255)   # laranja
        elif progress < 0.9:
            bar_color = (0, 220, 180)   # ciano
        else:
            bar_color = (0, 255, 80)    # verde (quase lá!)
        cv2.rectangle(frame, (bar_x, bar_y),
                      (bar_x + fill_w, bar_y + bar_h), bar_color, -1)

        # Borda da barra
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h),
                      (180, 180, 180), 1)

        # ── Texto: "Segure... Xs" ────────────────────────────────────────
        label_hold = f"Segure o braco levantado... {restante:.1f}s"
        (tw, _), _ = cv2.getTextSize(label_hold, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        tx = (w - tw) // 2
        cv2.putText(frame, label_hold, (tx, bar_y - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 220, 100), 2, cv2.LINE_AA)

        # ── Número grande de contagem regressiva no centro da tela ───────
        num_str = f"{int(restante) + 1}"
        font_scale = 3.5
        thickness  = 6
        (nw, nh), _ = cv2.getTextSize(num_str, cv2.FONT_HERSHEY_DUPLEX,
                                       font_scale, thickness)
        nx = (w - nw) // 2
        ny = int(h * 0.52) + nh // 2
        # Sombra
        cv2.putText(frame, num_str, (nx + 3, ny + 3),
                    cv2.FONT_HERSHEY_DUPLEX, font_scale, (0, 0, 0), thickness + 2,
                    cv2.LINE_AA)
        # Texto principal (cor muda conforme tempo)
        num_color = bar_color
        cv2.putText(frame, num_str, (nx, ny),
                    cv2.FONT_HERSHEY_DUPLEX, font_scale, num_color, thickness,
                    cv2.LINE_AA)

    def stop(self):
        self.running = False



# ─────────────────────────────────────────────
#  GATILHO POR GESTO (YOLO POSE — braço levantado)
# ─────────────────────────────────────────────

# Conexoes do esqueleto para desenho manual
_POSE_CONNECTIONS = [
    (5, 6),             # ombro esquerdo -> ombro direito
    (5, 7), (7, 9),     # braço esquerdo (ombro -> cotovelo -> pulso)
    (6, 8), (8, 10),    # braço direito (ombro -> cotovelo -> pulso)
    (5, 11), (6, 12),   # tronco (ombro -> quadril)
    (11, 12),           # quadril esquerdo -> quadril direito
    (11, 13), (13, 15), # perna esquerda (quadril -> joelho -> tornozelo)
    (12, 14), (14, 16)  # perna direita (quadril -> joelho -> tornozelo)
]

class GestureThread(threading.Thread):
    """
    Detecta o gesto de 'Braço Levantado' usando o YOLOv8-pose.
    Baixa o modelo automaticamente na primeira execucao (~6 MB).
    Se qualquer jogador na quadra levantar o braço acima da cabeça, dispara o replay.
    """

    def __init__(self, buf: 'ReplayBuffer'):
        super().__init__(daemon=True)
        self.buf             = buf
        self.running         = False
        # _gesture_hold_start: timestamp em que o braço foi levantado pela primeira vez
        # (0.0 = braço não está levantado ou gesto já disparou)
        self._gesture_hold_start = 0.0
        # Nota: _feedback_until é @property lida de _result_cache["feedback_until"]
        # O valor inicial (0.0) está declarado no _result_cache abaixo.

        # ── Pipeline de frames desacoplado ──────────────────────────────
        # queue.Queue(maxsize=1): put_nowait() descarta frames antigos
        # e mantém SEMPRE o frame mais recente disponível para inferência.
        # Elimina o gargalo de frame.copy() + Lock na thread de captura.
        self._frame_queue    = queue.Queue(maxsize=1)

        # ── Resultados: cache atômico sem lock na leitura ────────────────
        # A GestureThread escreve um objeto imutável de uma vez.
        # A CaptureThread lê via referência — sem contenção de lock.
        self._result_cache   = {          # referência substituída atomicamente
            "skeletons": [],
            "gesture": None,
            "persons": 0,
            "feedback_until": 0.0,
            "gesture_start": 0.0,   # timestamp do início do hold atual (0=sem hold)
        }

        # Compat: mantém latest_result_lock como no-op para não quebrar
        # código de leitura que ainda usa o lock por segurança
        self.latest_result_lock = threading.Lock()

        # Controle de estado para ativacao unica por gesto
        self._gesture_active  = False  # True quando o gesto atual ja disparou
        self._last_seen_at    = 0.0    # ultima vez que um braço levantado foi visto
        self._cooldown_until  = 0.0    # nao aceita novo disparo por gesto antes disso

        # Limita a taxa de inferencia (YOLO_FPS) — descarta frames ja na captura
        self._min_interval    = 1.0 / YOLO_FPS if YOLO_FPS > 0 else 0.0
        self._last_post_at    = 0.0

    @property
    def latest_skeletons(self):
        return self._result_cache["skeletons"]

    @property
    def latest_gesture(self):
        return self._result_cache["gesture"]

    @property
    def latest_persons_count(self):
        return self._result_cache["persons"]

    @property
    def _feedback_until(self):
        return self._result_cache["feedback_until"]

    def post_frame(self, frame):
        """Envia frame para processamento YOLO de forma não-bloqueante.

        Usa put_nowait() com descarte do frame anterior se a fila estiver
        cheia — garante que a GestureThread sempre processe o frame mais
        recente, sem nunca bloquear a CaptureThread.

        O resize para YOLO_INPUT_SIZE acontece aqui (fora do lock da captura)
        para desacoplar o custo de CPU da thread principal.
        """
        if not self.running:
            return
        # Throttle: sem isso o YOLO roda o mais rapido possivel e satura a CPU
        agora = time.time()
        if agora - self._last_post_at < self._min_interval:
            return
        self._last_post_at = agora
        # Redimensiona para YOLO_INPUT_SIZE
        # Feito na CaptureThread para evitar cópia extra dentro da GestureThread
        h, w = frame.shape[:2]
        if w > YOLO_INPUT_SIZE:
            scale = YOLO_INPUT_SIZE / w
            small = cv2.resize(frame, (YOLO_INPUT_SIZE, int(h * scale)),
                               interpolation=cv2.INTER_AREA)
        else:
            small = frame

        try:
            self._frame_queue.put_nowait(small)
        except queue.Full:
            # Fila cheia: descarta frame antigo e coloca o mais recente
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frame_queue.put_nowait(small)
            except queue.Full:
                pass  # abandona silenciosamente, próximo frame virá

    @staticmethod
    def _bipe(sequencia):
        """Toca bipes no PC sem bloquear a thread. sequencia = [(freq_hz, dur_ms), ...]"""
        if not GESTURE_SOUND:
            return
        def _tocar():
            try:
                import winsound
                for freq, dur in sequencia:
                    winsound.Beep(freq, dur)
            except Exception:
                pass
        threading.Thread(target=_tocar, daemon=True).start()

    @staticmethod
    def _braco_levantado(xy, conf):
        """Decide se UMA pessoa esta com o braço levantado (coordenadas COCO-17).

        Referencia de altura, em ordem de preferencia:
          1. Linha da cabeca = ponto mais baixo entre nariz, olhos e orelhas visiveis
             (~altura do nariz, mesmo criterio de antes). Usar varios pontos (e nao
             so o nariz) funciona com o jogador de costas para a camera, quando o
             nariz nao aparece.
          2. Sem cabeca visivel: o ombro do mesmo lado, com margem de meia
             largura de ombros (o pulso precisa estar bem acima do ombro).
        O pulso eh o ponto principal; o cotovelo acima da cabeca tambem conta,
        porque o pulso costuma sumir com motion blur quando a mao balança.
        """
        kp = GESTURE_KP_CONF
        head_ys = [xy[i, 1] for i in (0, 1, 2, 3, 4) if conf[i] > kp]
        linha_cabeca = max(head_ys) if head_ys else None   # Y maior = mais baixo na tela

        ombros_ok = conf[5] > kp and conf[6] > kp
        margem = abs(xy[5, 0] - xy[6, 0]) * 0.5 if ombros_ok else 0.0

        for pulso, cotovelo, ombro in ((9, 7, 5), (10, 8, 6)):
            if linha_cabeca is not None:
                if conf[pulso] > kp and xy[pulso, 1] < linha_cabeca:
                    return True
                if conf[cotovelo] > kp and xy[cotovelo, 1] < linha_cabeca:
                    return True
            elif conf[pulso] > kp and conf[ombro] > kp and margem > 0:
                if xy[pulso, 1] < xy[ombro, 1] - margem:
                    return True
        return False

    def _disparar_gesto(self):
        """Dispara a gravacao imediatamente e ativa o estado do gesto."""
        agora = time.time()
        self._gesture_active     = True
        self._gesture_hold_start = 0.0   # reseta hold para próximo ciclo
        self._cooldown_until     = agora + GESTURE_DEBOUNCE
        # Atualiza feedback_until no cache atomicamente
        self._result_cache = {**self._result_cache,
                              "feedback_until": agora + 1.5,
                              "gesture_start": 0.0}

        # Dois bipes agudos = replay disparado
        self._bipe([(1400, 150), (1800, 300)])

        # Reseta o debounce temporal do buffer para gravar imediatamente
        self.buf._last_save_at = 0
        self.buf.trigger_save("gesto")
        self.buf.release_key()

    def run(self):
        if not YOLO_OK:
            log("[GESTO] Ultralytics nao instalado — detecção de pose desativada.", YELLOW)
            return

        # ── Inicializa YOLO Pose ──
        yolo_detector = None
        try:
            log(f"[GESTO] Carregando YOLOv8-pose (imgsz={YOLO_INPUT_SIZE}, {YOLO_FPS} inferencias/s, "
                f"device={YOLO_DEVICE})...", YELLOW)
            if YOLO_THREADS > 0:
                import torch
                torch.set_num_threads(YOLO_THREADS)   # deixa nucleos livres para captura/ffmpeg
            yolo_detector = _YOLO_CLASS(YOLO_MODEL)   # baixa ~6MB na 1a vez

            import numpy as _np
            # Warmup com tamanho fixo — elimina resize dinâmico interno a cada frame
            # show=False: impede o Ultralytics de abrir janela OpenCV própria
            warmup_frame = _np.zeros((YOLO_INPUT_SIZE, YOLO_INPUT_SIZE, 3), dtype='uint8')
            yolo_detector(
                warmup_frame,
                imgsz=YOLO_INPUT_SIZE,
                device=YOLO_DEVICE,
                half=YOLO_HALF,
                show=False,
                verbose=False
            )
            log("[GESTO] YOLO Pose pronto! Qualquer jogador pode levantar o braço para salvar replay.", GREEN)
        except Exception as e:
            log(f"[GESTO] Falha ao carregar YOLO Pose: {e}", RED)
            return

        self.running = True
        while self.running:
            try:
                # Aguarda frame da CaptureThread (timeout=100ms para checar self.running)
                try:
                    frame = self._frame_queue.get(timeout=0.10)
                except queue.Empty:
                    continue
                if frame is None:
                    continue

                # Roda detecção de pose com parâmetros fixos de performance:
                # - imgsz fixo: evita resize dinâmico interno do YOLO a cada frame
                # - device fixo: evita re-detecção de hardware a cada inferência
                # - show=False: impede janela própria do Ultralytics (causa 2ª janela cinza)
                resultados = yolo_detector(
                    frame,
                    imgsz=YOLO_INPUT_SIZE,
                    conf=YOLO_CONFIDENCE,
                    device=YOLO_DEVICE,
                    half=YOLO_HALF,
                    show=False,
                    verbose=False
                )
                
                keypoints_data = resultados[0].keypoints
                skeletons_data = []
                gesto_ok = False
                n_pessoas = 0

                if keypoints_data is not None and len(keypoints_data) > 0:
                    xy = keypoints_data.xy.cpu().numpy()     # shape: (N, 17, 2)
                    conf = keypoints_data.conf.cpu().numpy() # shape: (N, 17)
                    n_pessoas = len(xy)

                    fh, fw = frame.shape[:2]
                    for i in range(n_pessoas):
                        pts_norm = []
                        for pt_idx in range(17):
                            pts_norm.append({
                                'x': xy[i, pt_idx, 0] / fw,
                                'y': xy[i, pt_idx, 1] / fh,
                                'conf': conf[i, pt_idx]
                            })
                        
                        braço_lev = self._braco_levantado(xy[i], conf[i])

                        skeletons_data.append({
                            'points': pts_norm,
                            'raising_arm': braço_lev
                        })

                    gesto_ok = any(sk['raising_arm'] for sk in skeletons_data)

                # Publica resultados atomicamente: substitui o dict inteiro.
                # A CaptureThread lê _result_cache sem lock — race condition
                # é aceitável aqui (pior caso: lê resultado 1 frame antigo).
                self._result_cache = {
                    "skeletons": skeletons_data,
                    "gesture": "Arm_Raised" if gesto_ok else None,
                    "persons": n_pessoas,
                    "feedback_until": self._result_cache["feedback_until"],
                    "gesture_start": self._gesture_hold_start,
                }

                self._atualizar_hold(gesto_ok, time.time())

            except Exception as e:
                log(f"[GESTO] Erro no loop de detecção: {e}", RED)
                time.sleep(0.03)
                continue

        log("[GESTO] Thread encerrada.", CYAN)

    def _atualizar_hold(self, gesto_ok, agora):
        """Maquina de estados do hold: inicia, confirma apos GESTURE_HOLD_SECS ou cancela."""
        if gesto_ok:
            self._last_seen_at = agora
            if not self._gesture_active and agora >= self._cooldown_until:
                # Inicia cronômetro na primeira detecção do hold
                if self._gesture_hold_start == 0.0:
                    self._gesture_hold_start = agora
                    self._bipe([(900, 120)])   # bipe curto = hold comecou
                    log("[GESTO] Braço levantado detectado — mantendo por "
                        f"{GESTURE_HOLD_SECS:.0f}s para confirmar...", YELLOW)

                # Verifica se o tempo mínimo de hold foi atingido
                elapsed = agora - self._gesture_hold_start
                if elapsed >= GESTURE_HOLD_SECS:
                    self._disparar_gesto()
        elif agora - self._last_seen_at > GESTURE_GRACE_SECS:
            # Braço sumiu por mais que a tolerância: cancela o hold e
            # rearma o gatilho. Falhas curtas (blur, oclusão, YOLO
            # perdendo o pulso por alguns frames) NÃO cancelam o hold.
            if self._gesture_hold_start > 0.0 and not self._gesture_active:
                log("[GESTO] Braço abaixou antes de completar o hold. Reiniciando.", YELLOW)
            self._gesture_hold_start = 0.0
            self._gesture_active = False
            self.buf.release_key()

    def stop(self):
        self.running = False


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def _ler_argumentos():
    """--camera <indice|url> e --nome <texto> (passados pelo iniciar.py)."""
    import argparse
    p = argparse.ArgumentParser(description="Instant Replay System")
    p.add_argument("--camera", help="indice da camera local ou URL rtsp://")
    p.add_argument("--nome", help="nome da camera (apenas exibicao)")
    args = p.parse_args()
    if args.camera is None:
        return CAMERA_SOURCE, None
    fonte = int(args.camera) if args.camera.isdigit() else args.camera
    return fonte, args.nome


def main():
    global CAMERA_SOURCE
    CAMERA_SOURCE, nome_camera = _ler_argumentos()
    descricao_camera = f"{nome_camera} ({CAMERA_SOURCE})" if nome_camera else CAMERA_SOURCE

    print(f"""
{CYAN}{BOLD}=======================================================
  INSTANT REPLAY SYSTEM - IP Camera
======================================================={RESET}

  Camera   : {descricao_camera}
  Buffer   : {REPLAY_SECONDS} segundos
  Output   : {os.path.abspath(OUTPUT_DIR)}/
  Hotkey   : [ESPACO]  <- um toque salva, segurar nao repete
  Gesto    : Braço Levantado via YOLO Pose {'(ATIVO)' if GESTURE_ENABLED and YOLO_OK else '(INATIVO — instale ultralytics)'}

{YELLOW}  Aguarde o buffer encher antes do primeiro replay.
  Pressione [Q] na janela ou Ctrl+C no terminal para sair.{RESET}
""")
    limpar_replays(mostrar_uso=True)

    buf = ReplayBuffer()

    # Inicia thread de gestos baseada em YOLO Pose
    gesture_thr = None
    if GESTURE_ENABLED and YOLO_OK:
        gesture_thr = GestureThread(buf)
        gesture_thr.start()
    elif GESTURE_ENABLED and not YOLO_OK:
        log("[GESTO] Ultralytics nao encontrado. Rode: pip install ultralytics", YELLOW)

    cap_thr  = CaptureThread(CAMERA_SOURCE, buf, gesture_thr)
    listener = start_keyboard_listener(buf)

    cap_thr.start()

    try:
        cap_thr.join()
    except KeyboardInterrupt:
        log("Encerrando...", YELLOW)
        cap_thr.stop()

    if gesture_thr:
        gesture_thr.stop()

    listener.stop()
    log("Sistema encerrado.", CYAN)


if __name__ == "__main__":
    main()