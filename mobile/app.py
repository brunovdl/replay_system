"""
Replay Mobile — servidor (Easypanel)

O celular filma, mantem os ultimos 30s ja comprimidos (H.264 do hardware) e,
quando alguem dispara, envia so o clipe para ca. O servidor:
  1. recebe o clipe (H.264 Annex B cru + metadados)
  2. gera o MP4 compativel com WhatsApp (mesmo comando ffmpeg do replay_cam.py)
  3. encaminha ao webhook do n8n (mesmos campos: file, duracao, evento)

Variaveis de ambiente:
  REPLAY_TOKEN     senha exigida pela pagina (vazio = sem senha, so para teste local)
  N8N_WEBHOOK_URL  webhook do n8n (vazio = so salva, nao envia). Aceita tambem WEBHOOK_URL
  DATA_DIR         onde guardar os replays (padrao: ./data)
  KEEP_DAYS        apaga replays com mais de N dias (padrao: 7; 0 = nunca)
  MAX_STORAGE_GB   limite de espaco dos replays; acima dele apaga os mais antigos (padrao: 5; 0 = sem limite)

Os arquivos podem ser vistos, baixados e apagados em /arquivos.html.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime

import requests
from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

REPLAY_TOKEN    = os.environ.get("REPLAY_TOKEN", "")
# WEBHOOK_URL: nome usado pelo servico antigo (Replay 2.0) no Easypanel
N8N_WEBHOOK_URL = os.environ.get("N8N_WEBHOOK_URL") or os.environ.get("WEBHOOK_URL", "")
DATA_DIR        = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
KEEP_DAYS       = float(os.environ.get("KEEP_DAYS", "7"))
MAX_STORAGE_GB  = float(os.environ.get("MAX_STORAGE_GB", "5"))
MAX_UPLOAD_MB   = 60      # 30s a ~2,5 Mbps ~= 10 MB; folga para bitrate alto
STATIC_DIR      = os.path.join(os.path.dirname(__file__), "static")

os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI(title="Replay Mobile")

# Status dos replays em memoria: id -> {"estado", "mensagem", ...}
# Estados: recebido -> convertendo -> enviando -> concluido | erro
_status = {}
_status_lock = threading.Lock()
# Um ffmpeg por vez: a VPS nao trava se chegarem varios replays juntos
_ffmpeg_lock = threading.Lock()


def _set_status(rid, **campos):
    with _status_lock:
        _status.setdefault(rid, {}).update(campos, atualizado=time.time())


def _log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _checar_token(token):
    if REPLAY_TOKEN and token != REPLAY_TOKEN:
        raise HTTPException(status_code=401, detail="Senha invalida")


# So estes arquivos podem ser listados/baixados/apagados pela pagina de gestao
# (o nome vem da URL: nada de "../", telemetria ou outros arquivos do volume)
_NOME_VIDEO = re.compile(r"^(replay_zap_[\w-]+\.mp4|bruto_[\w-]+\.h264)$")


def _videos():
    """Videos do volume, do mais novo para o mais antigo."""
    itens = []
    for nome in os.listdir(DATA_DIR):
        caminho = os.path.join(DATA_DIR, nome)
        if not _NOME_VIDEO.match(nome) or not os.path.isfile(caminho):
            continue
        try:
            st = os.stat(caminho)
        except OSError:
            continue
        itens.append({"nome": nome, "tipo": "replay" if nome.endswith(".mp4") else "falha",
                      "tamanho": st.st_size, "criado": st.st_mtime})
    itens.sort(key=lambda i: i["criado"], reverse=True)
    return itens


def _caminho_video(nome):
    if not _NOME_VIDEO.match(nome):
        raise HTTPException(status_code=400, detail="nome invalido")
    caminho = os.path.join(DATA_DIR, nome)
    if not os.path.isfile(caminho):
        raise HTTPException(status_code=404, detail="arquivo nao encontrado")
    return caminho


def _apagar(nome, motivo):
    try:
        os.remove(os.path.join(DATA_DIR, nome))
        _log(f"Apagado ({motivo}): {nome}")
        return True
    except OSError:
        return False


def _limpar_antigos():
    """Apaga videos com mais de KEEP_DAYS dias e, passando de MAX_STORAGE_GB, os mais antigos."""
    agora = time.time()
    restantes = []
    for v in _videos():
        if KEEP_DAYS > 0 and v["criado"] < agora - KEEP_DAYS * 86400:
            _apagar(v["nome"], "antigo")
        else:
            restantes.append(v)
    if MAX_STORAGE_GB > 0:
        total = sum(v["tamanho"] for v in restantes)
        for v in reversed(restantes):          # do mais antigo para o mais novo
            if total <= MAX_STORAGE_GB * 1024 ** 3:
                break
            if v["criado"] > agora - 300:      # recem-chegado: pode estar sendo convertido/enviado
                continue
            if _apagar(v["nome"], "limite de espaco"):
                total -= v["tamanho"]


def _processar(rid, bruto, meta):
    """Converte o H.264 cru em MP4 do WhatsApp e envia ao n8n (roda em background)."""
    ts      = meta["ts"]
    final   = os.path.join(DATA_DIR, f"replay_zap_{ts}.mp4")
    fps     = meta["fps"]
    duracao = meta["duracao"]
    evento  = meta["evento"]
    try:
        _set_status(rid, estado="convertendo")
        with _ffmpeg_lock:
            # Mesmo pipeline do replay_cam.py. Diferenca: a entrada eh H.264 cru
            # (sem timestamps), entao o fps medido no celular define a velocidade.
            cmd = [
                shutil.which("ffmpeg") or "ffmpeg", "-y",
                "-f", "h264", "-framerate", f"{fps:.3f}", "-i", bruto,
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
                final,
            ]
            t0 = time.time()
            res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg ({res.returncode}): {res.stderr[-300:]}")
        _log(f"[{rid}] MP4 pronto em {time.time() - t0:.1f}s: {os.path.basename(final)}")
        os.remove(bruto)

        if not N8N_WEBHOOK_URL:
            _set_status(rid, estado="concluido", mensagem="Salvo no servidor (n8n nao configurado)",
                        arquivo=os.path.basename(final))
            return

        _set_status(rid, estado="enviando")
        with open(final, "rb") as f:
            resp = requests.post(
                N8N_WEBHOOK_URL,
                files={"file": (os.path.basename(final), f, "video/mp4")},
                data={"duracao": f"{duracao:.1f}", "evento": evento},
                timeout=60,
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"n8n retornou HTTP {resp.status_code}: {resp.text[:100]}")
        _log(f"[{rid}] Enviado ao n8n")
        _set_status(rid, estado="concluido", mensagem="Enviado para o WhatsApp",
                    arquivo=os.path.basename(final))
    except Exception as e:
        _log(f"[{rid}] ERRO: {e}")
        _set_status(rid, estado="erro", mensagem=str(e)[:200])
        # MP4 incompleto nao serve para nada; o bruto fica para diagnostico (apagado apos KEEP_DAYS)
        if os.path.exists(final) and os.path.exists(bruto):
            os.remove(final)
    finally:
        _limpar_antigos()


_limpar_antigos()   # ao subir o container, sem esperar o proximo replay


@app.get("/api/health")
def health():
    return {"ok": True, "ffmpeg": bool(shutil.which("ffmpeg")), "n8n": bool(N8N_WEBHOOK_URL),
            "senha": bool(REPLAY_TOKEN)}


@app.post("/api/login")
def login(x_replay_token: str = Header(default="")):
    """Usado pela pagina para validar a senha antes de comecar a filmar."""
    _checar_token(x_replay_token)
    return {"ok": True}


@app.post("/api/replay", status_code=202)
async def receber_replay(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    meta: str = Form(...),
    x_replay_token: str = Header(default=""),
):
    _checar_token(x_replay_token)
    try:
        m = json.loads(meta)
        fps     = float(m["fps"])
        duracao = float(m["duracao"])
        evento  = str(m.get("evento", "celular"))[:30]
    except Exception:
        raise HTTPException(status_code=400, detail="meta invalido")
    if not (1 <= fps <= 120):
        raise HTTPException(status_code=400, detail="fps fora do intervalo")

    rid = uuid.uuid4().hex[:10]
    # Timestamp do disparo no celular (nao da chegada, que pode atrasar no 4G)
    try:
        disparo = datetime.fromtimestamp(float(m["disparado_em"]) / 1000)
        if abs((datetime.now() - disparo).days) > 2:   # relogio do celular errado
            raise ValueError
    except Exception:
        disparo = datetime.now()
    ts = disparo.strftime("%Y%m%d_%H%M%S")
    ts = f"{ts}_{rid[:4]}"

    bruto = os.path.join(DATA_DIR, f"bruto_{ts}.h264")
    tamanho = 0
    with open(bruto, "wb") as out:
        while bloco := await file.read(1024 * 1024):
            tamanho += len(bloco)
            if tamanho > MAX_UPLOAD_MB * 1024 * 1024:
                out.close()
                os.remove(bruto)
                raise HTTPException(status_code=413, detail="clipe grande demais")
            out.write(bloco)

    _log(f"[{rid}] Recebido: {tamanho / 1e6:.1f} MB, {duracao:.1f}s @ {fps:.1f} fps ({evento})")
    _set_status(rid, estado="recebido", mensagem="")
    background.add_task(_processar, rid, bruto, {"ts": ts, "fps": fps, "duracao": duracao, "evento": evento})
    return {"id": rid}


@app.get("/api/replay/{rid}")
def status_replay(rid: str, x_replay_token: str = Header(default="")):
    _checar_token(x_replay_token)
    with _status_lock:
        st = _status.get(rid)
    if st is None:
        raise HTTPException(status_code=404, detail="replay nao encontrado")
    return st


@app.post("/api/telemetria")
async def telemetria(request: Request, x_replay_token: str = Header(default="")):
    """Registra bateria/fps a cada minuto: base para avaliar o teste de campo."""
    _checar_token(x_replay_token)
    dados = await request.json()
    linha = {"hora": datetime.now().isoformat(timespec="seconds"), **dados}
    with open(os.path.join(DATA_DIR, "telemetria.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(linha, ensure_ascii=False) + "\n")
    return {"ok": True}


@app.get("/api/arquivos")
def listar_arquivos(x_replay_token: str = Header(default="")):
    _checar_token(x_replay_token)
    videos = _videos()
    disco = shutil.disk_usage(DATA_DIR)
    return {
        "arquivos": videos,
        "total": sum(v["tamanho"] for v in videos),
        "disco_livre": disco.free,
        "keep_days": KEEP_DAYS,
        "max_storage_gb": MAX_STORAGE_GB,
    }


@app.get("/api/arquivos/{nome}")
def baixar_arquivo(nome: str, x_replay_token: str = Header(default="")):
    _checar_token(x_replay_token)
    tipo = "video/mp4" if nome.endswith(".mp4") else "application/octet-stream"
    return FileResponse(_caminho_video(nome), media_type=tipo, filename=nome)


@app.post("/api/arquivos/apagar")
def apagar_arquivos(dados: dict = Body(...), x_replay_token: str = Header(default="")):
    """{"nomes": [...]} apaga esses videos; {"todos": true} apaga todos."""
    _checar_token(x_replay_token)
    if dados.get("todos"):
        nomes = [v["nome"] for v in _videos()]
    else:
        nomes = [n for n in dados.get("nomes", []) if isinstance(n, str) and _NOME_VIDEO.match(n)]
    apagados = sum(_apagar(n, "pela pagina") for n in nomes)
    return {"ok": True, "apagados": apagados}


@app.get("/")
def index():
    # no-cache: atualizacoes da pagina chegam ao celular sem limpar o cache
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
