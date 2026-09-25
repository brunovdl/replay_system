import os
import shutil
import subprocess
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger("replay.converter")

def get_ffmpeg_executable() -> str:
    """
    Retorna o executável do FFmpeg disponível.
    1. Procura no PATH do sistema operacional (típico em Docker/Linux).
    2. Fallback para imageio-ffmpeg se disponível (automático no Windows).
    """
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception as e:
        logger.warning(f"imageio-ffmpeg não pôde ser carregado: {e}")

    raise RuntimeError(
        "FFmpeg não foi encontrado no PATH do sistema e imageio-ffmpeg não está instalado."
    )

def concatenate_and_convert(chunk_paths: List[Path], output_path: Path) -> Path:
    """
    Concatena os fragmentos de vídeo recebidos do frontend e converte
    para um MP4 universalmente compatível com WhatsApp (H.264 + AAC + faststart).
    """
    if not chunk_paths:
        raise ValueError("Nenhum fragmento de vídeo fornecido para conversão.")

    ffmpeg_bin = get_ffmpeg_executable()
    logger.info(f"Usando binário FFmpeg: {ffmpeg_bin}")

    work_dir = chunk_paths[0].parent

    if len(chunk_paths) == 1:
        # Apenas 1 arquivo para transcodificar
        input_file = chunk_paths[0]
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i", str(input_file),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path)
        ]
    else:
        # Múltiplos fragmentos: cria lista para o demuxer concat
        list_file = work_dir / "concat_list.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for p in chunk_paths:
                # Usa barras normais para compatibilidade no FFmpeg mesmo no Windows
                safe_path = str(p.resolve()).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path)
        ]

    logger.info(f"Executando FFmpeg: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if result.returncode != 0:
        logger.error(f"Erro no FFmpeg (código {result.returncode}):\n{result.stderr}")
        raise RuntimeError(f"FFmpeg falhou ao converter vídeo: {result.stderr[-500:]}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("Arquivo final de vídeo não foi gerado ou está vazio.")

    logger.info(f"Vídeo convertido com sucesso ({output_path.stat().st_size} bytes): {output_path}")
    return output_path
