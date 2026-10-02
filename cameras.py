"""
Descoberta de cameras locais (DirectShow) e suporte ao Iriun Webcam.

A ordem dos dispositivos listados aqui eh a mesma que o OpenCV usa com
cv2.CAP_DSHOW, entao a posicao na lista eh o indice para cv2.VideoCapture.
"""

import os
import re
import subprocess
import time

import psutil

IRIUN_EXE = r"C:\Program Files (x86)\Iriun Webcam\IriunWebcam.exe"


def _ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def listar_cameras():
    """Retorna os nomes das cameras de video do Windows, na ordem do DirectShow.

    Usa 'ffmpeg -list_devices' porque o OpenCV nao informa nomes de dispositivos.
    Retorna [] se o ffmpeg nao estiver disponivel.
    """
    try:
        r = subprocess.run(
            [_ffmpeg_exe(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, timeout=15, stdin=subprocess.DEVNULL,
        )
    except Exception:
        return []
    saida = r.stderr.decode("utf-8", errors="replace")
    # Formato: [dshow @ 0x...] "Nome da Camera" (video)
    return re.findall(r'"([^"]+)"\s+\(video\)', saida)


def eh_iriun(nome):
    return "iriun" in (nome or "").lower()


def iriun_rodando():
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() == "iriunwebcam.exe":
                return True
        except Exception:
            pass
    return False


def garantir_iriun(espera=5, log=print):
    """Abre o app Iriun Webcam se ele nao estiver rodando.

    Sem o app aberto, o dispositivo 'Iriun Webcam' existe mas so entrega uma
    imagem de espera. Retorna True se o app estiver (ou ficar) rodando.
    """
    if iriun_rodando():
        log("[OK]  Iriun Webcam ja esta aberto.")
        return True
    if not os.path.exists(IRIUN_EXE):
        log(f"[AVISO] Iriun nao encontrado em {IRIUN_EXE}. Abra o app manualmente.")
        return False
    log("[>>]  Abrindo Iriun Webcam...")
    subprocess.Popen([IRIUN_EXE], cwd=os.path.dirname(IRIUN_EXE))
    time.sleep(espera)
    log("[!]   Abra o app Iriun no celular para conectar a camera.")
    return iriun_rodando()
