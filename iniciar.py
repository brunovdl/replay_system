"""
LAUNCHER - Replay System
Pergunta qual camera usar (Iriun/celular, webcam do PC, outras detectadas
ou a camera IP Xiaomi) e inicia o replay_cam com ela.
- Iriun: abre o app IriunWebcam.exe se nao estiver rodando.
- Xiaomi: descobre o IP via ARP scan e sobe o go2rtc (RTSP local).
"""

import subprocess
import socket
import time
import sys
import os
import re
import json
import concurrent.futures
import psutil
import urllib.request

import cameras

# Forca UTF-8 no stdout do Windows (evita UnicodeEncodeError)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ─────────────────────────────────────────────
#  CONFIGURACOES GERAIS
# ─────────────────────────────────────────────
GO2RTC_EXE         = "go2rtc.exe"
GO2RTC_CONFIG      = "go2rtc.yaml"
REPLAY_SCRIPT      = "replay_cam.py"
CAMERA_CONFIG_FILE = "camera_config.json"

RTSP_HOST     = "localhost"
RTSP_PORT     = 8554
RTSP_URL      = f"rtsp://{RTSP_HOST}:{RTSP_PORT}/camera"   # stream 'camera' do go2rtc.yaml
FONTE_XIAOMI  = "Camera IP Xiaomi (via go2rtc)"
WAIT_TIMEOUT  = 20    # segundos aguardando go2rtc subir
WAIT_INTERVAL = 0.4   # intervalo entre verificacoes

# Porta nativa do protocolo Xiaomi (usada para checar se camera responde)
XIAOMI_PORT   = 54321

# ─────────────────────────────────────────────
#  CORES ANSI
# ─────────────────────────────────────────────
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def log(msg, color=RESET):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}{BOLD}[{ts}]{RESET} {color}{msg}{RESET}", flush=True)


# ─────────────────────────────────────────────
#  CONFIG DA CAMERA (persistente)
# ─────────────────────────────────────────────
def carregar_config_camera():
    """Carrega camera_config.json. Cria com valores padrao se nao existir."""
    padrao = {"mac": "", "ultimo_ip": ""}
    if not os.path.exists(CAMERA_CONFIG_FILE):
        return padrao
    try:
        with open(CAMERA_CONFIG_FILE, 'r', encoding='utf-8') as f:
            dados = json.load(f)
        return {**padrao, **dados}
    except Exception:
        return padrao

def salvar_config_camera(config):
    """Persiste o MAC e ultimo IP descoberto, preservando campos extras como 'notas'."""
    try:
        existente = {}
        if os.path.exists(CAMERA_CONFIG_FILE):
            with open(CAMERA_CONFIG_FILE, 'r', encoding='utf-8') as f:
                existente = json.load(f)
        # Atualiza apenas os campos de controle, nunca sobrescreve 'notas' ou similares
        for k, v in config.items():
            if k not in ('notas',):
                existente[k] = v
        with open(CAMERA_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(existente, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"[AVISO] Nao foi possivel salvar camera_config.json: {e}", YELLOW)


# ─────────────────────────────────────────────
#  DESCOBERTA DINAMICA DE IP
# ─────────────────────────────────────────────
def camera_respondendo(ip, timeout=1.5):
    """
    Verifica se o IP dado responde.
    Tenta a porta Xiaomi (54321) e fallback via ping ICMP.
    """
    # Tentativa 1: porta Xiaomi
    try:
        with socket.create_connection((ip, XIAOMI_PORT), timeout=timeout):
            return True
    except OSError:
        pass

    # Tentativa 2: ping ICMP (funciona mesmo que porta esteja filtrada)
    r = subprocess.run(
        ['ping', '-n', '1', '-w', str(int(timeout * 1000)), ip],
        capture_output=True
    )
    return r.returncode == 0


def obter_mac_por_ip(ip):
    """
    Extrai o MAC address de um IP consultando a tabela ARP do Windows.
    Retorna string no formato 'AA:BB:CC:DD:EE:FF' ou '' se nao encontrar.
    """
    r = subprocess.run(['arp', '-a', ip], capture_output=True, text=True)
    # Padrao: XX-XX-XX-XX-XX-XX (formato Windows)
    m = re.search(r'([0-9a-f]{2}-){5}[0-9a-f]{2}', r.stdout, re.IGNORECASE)
    if m:
        return m.group(0).upper().replace('-', ':')
    return ''


def _ping_worker(ip, timeout_ms=200):
    """Worker para ping paralelo (popula tabela ARP sem aguardar resposta)."""
    subprocess.run(
        ['ping', '-n', '1', '-w', str(timeout_ms), ip],
        capture_output=True
    )


def scan_subnet_arp(subnet, mac_alvo):
    """
    Faz ping paralelo em toda a subnet para popular a tabela ARP,
    depois busca o dispositivo com o MAC informado.
    Retorna o IP encontrado ou ''.
    """
    log(f"[...] Varrendo rede {subnet}.0/24 em busca da camera...", YELLOW)

    # Ping em todos os hosts em paralelo (rapido, ~3-5s)
    ips = [f"{subnet}.{i}" for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=80) as pool:
        pool.map(_ping_worker, ips)

    # Ler tabela ARP completa
    r = subprocess.run(['arp', '-a'], capture_output=True, text=True)
    mac_busca = mac_alvo.upper().replace(':', '-')

    for linha in r.stdout.splitlines():
        if mac_busca.lower() in linha.lower():
            # Formato Windows: "  192.168.1.x   aa-bb-cc-dd-ee-ff   dinamico"
            partes = linha.split()
            for parte in partes:
                if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', parte):
                    return parte
    return ''


def descobrir_ip_camera(config):
    """
    Estrategia em cascata para encontrar o IP atual da camera:

    1. Testa o ultimo IP conhecido (< 2s se online)
       1a. Sucesso: salva MAC se ainda nao tiver, retorna IP
    2. Se falhou e temos MAC: faz ARP scan na subnet (~4s)
    3. Se sem MAC: avisa usuario e usa ultimo IP como fallback

    Retorna (ip_encontrado, mac_encontrado).
    """
    ultimo_ip = config.get("ultimo_ip", "").strip()
    mac       = config.get("mac", "").strip()

    # ── Estrategia 1: ultimo IP ainda funciona? ──
    if ultimo_ip:
        log(f"[...] Verificando camera no ultimo IP: {ultimo_ip}", CYAN)
        if camera_respondendo(ultimo_ip):
            log(f"[OK]  Camera encontrada em {ultimo_ip}", GREEN)
            # Auto-captura MAC na primeira vez (poupa scan futuro)
            if not mac:
                mac_detectado = obter_mac_por_ip(ultimo_ip)
                if mac_detectado:
                    log(f"[OK]  MAC registrado automaticamente: {mac_detectado}", GREEN)
                    mac = mac_detectado
            return ultimo_ip, mac

    # ── Estrategia 2: scan ARP por MAC ──
    if mac:
        # Determina subnet a partir do ultimo IP ou usa 192.168.1
        if ultimo_ip and ultimo_ip.count('.') == 3:
            subnet = '.'.join(ultimo_ip.split('.')[:3])
        else:
            subnet = '192.168.1'
            log(f"[AVISO] Sem IP anterior, varrendo subnet padrao {subnet}.0/24", YELLOW)

        log(f"[!]   IP mudou! Buscando pelo MAC {mac}...", YELLOW)
        novo_ip = scan_subnet_arp(subnet, mac)
        if novo_ip:
            log(f"[OK]  Camera encontrada no novo IP: {novo_ip}", GREEN)
            return novo_ip, mac
        else:
            log("[ERRO] Camera nao encontrada na rede local.", RED)
            return '', mac

    # ── Estrategia 3: sem MAC nem IP recente ──
    log("[AVISO] Sem MAC registrado. Execute o sistema uma vez com a camera online", YELLOW)
    log("        para registrar o MAC automaticamente.", YELLOW)
    if ultimo_ip:
        log(f"[...] Usando ultimo IP salvo como fallback: {ultimo_ip}", YELLOW)
        return ultimo_ip, mac

    return '', mac


# ─────────────────────────────────────────────
#  ATUALIZACAO DO go2rtc.yaml
# ─────────────────────────────────────────────
def atualizar_ip_go2rtc(yaml_path, novo_ip):
    """
    Substitui APENAS o IP da camera na URL do stream no go2rtc.yaml.
    Preserva todo o restante do arquivo intacto.

    Formato alvo: xiaomi://USER:REGION@<IP>?...
                             ou
                  xiaomi://USER@<IP>?...
    """
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            conteudo = f.read()

        # Regex cobre ambos os formatos e CRLF/LF do Windows:
        #   xiaomi://USER@IP?...
        #   xiaomi://USER:REGION@IP?...
        padrao = r'(xiaomi://[^@\r\n]+@)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'

        if not re.search(padrao, conteudo):
            log("[AVISO] Padrao de URL Xiaomi nao encontrado no YAML — verifique o formato.", YELLOW)
            return False

        novo_conteudo = re.sub(padrao, rf'\g<1>{novo_ip}', conteudo)

        if novo_conteudo == conteudo:
            log(f"[OK]  go2rtc.yaml ja esta com o IP correto: {novo_ip}", GREEN)
            return True

        with open(yaml_path, 'w', encoding='utf-8') as f:
            f.write(novo_conteudo)

        log(f"[OK]  go2rtc.yaml atualizado com IP: {novo_ip}", GREEN)
        return True

    except Exception as e:
        log(f"[ERRO] Falha ao atualizar go2rtc.yaml: {e}", RED)
        return False


# ─────────────────────────────────────────────
#  RTSP / go2rtc
# ─────────────────────────────────────────────
def porta_aberta(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (ConnectionRefusedError, OSError):
        return False

def aguardar_go2rtc(timeout=WAIT_TIMEOUT):
    log(f"[...] Aguardando go2rtc iniciar na porta {RTSP_PORT}...", YELLOW)
    inicio = time.time()
    while time.time() - inicio < timeout:
        if porta_aberta(RTSP_HOST, RTSP_PORT):
            elapsed = time.time() - inicio
            log(f"[OK]  go2rtc pronto! ({elapsed:.1f}s)", GREEN)
            return True
        time.sleep(WAIT_INTERVAL)
    return False


def pre_aquecer_stream(stream_name="camera_src", api_port=1984, espera=8):
    """Força o go2rtc a conectar no stream da câmera antes do replay_cam tentar.

    O go2rtc usa streams lazy: só conecta quando um cliente pede. Ao pré-aquecer
    o 'camera_src' via API, garantimos que a câmera Xiaomi já está transmitindo
    quando o FFmpeg iniciar a transcodificação para o stream 'camera'.
    """
    try:
        url = f"http://localhost:{api_port}/api/stream?src={stream_name}"
        log(f"[...] Pré-aquecendo stream '{stream_name}' no go2rtc...", YELLOW)
        req = urllib.request.Request(url, method="GET")
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass  # Falha silenciosa — não é crítico
    # Aguarda a câmera estabelecer conexão com o go2rtc
    log(f"[...] Aguardando câmera conectar ({espera}s)...", YELLOW)
    time.sleep(espera)
    log("[OK]  Câmera pré-aquecida. Iniciando sistema de replay.", GREEN)


# ─────────────────────────────────────────────
#  ESCOLHA DA CAMERA
# ─────────────────────────────────────────────
def escolher_camera(config):
    """Mostra as cameras detectadas e pergunta qual usar.

    Retorna (nome, indice). indice = posicao no DirectShow, ou None para a
    camera IP Xiaomi. ENTER repete a ultima escolha (salva por nome, porque o
    indice muda quando uma camera eh conectada/desconectada).
    """
    log("[...] Procurando cameras conectadas...", CYAN)
    locais = cameras.listar_cameras()
    opcoes = locais + [FONTE_XIAOMI]

    ultima = config.get("ultima_camera", "")
    padrao = opcoes.index(ultima) if ultima in opcoes else None
    if padrao is None:
        # Sem escolha anterior valida: sugere o Iriun (celular), se existir
        padrao = next((i for i, n in enumerate(opcoes) if cameras.eh_iriun(n)), 0)

    print(f"\n{BOLD}  Qual camera usar?{RESET}\n")
    for i, nome in enumerate(opcoes):
        marca = f"  {GREEN}<- ENTER{RESET}" if i == padrao else ""
        dica  = f" {CYAN}(celular){RESET}" if cameras.eh_iriun(nome) else ""
        print(f"   [{i + 1}] {nome}{dica}{marca}")
    if not locais:
        print(f"\n   {YELLOW}Nenhuma camera local detectada.{RESET}")

    while True:
        resp = input(f"\n  Numero da camera [{padrao + 1}]: ").strip()
        if resp == "":
            escolha = padrao
            break
        if resp.isdigit() and 1 <= int(resp) <= len(opcoes):
            escolha = int(resp) - 1
            break
        print(f"  {RED}Opcao invalida. Digite um numero de 1 a {len(opcoes)}.{RESET}")

    nome = opcoes[escolha]
    config["ultima_camera"] = nome
    salvar_config_camera(config)
    print()
    log(f"[OK]  Camera escolhida: {nome}", GREEN)
    return nome, (escolha if nome != FONTE_XIAOMI else None)


def preparar_xiaomi(config_cam, script_dir):
    """Descobre o IP da Xiaomi, atualiza o go2rtc.yaml e sobe o go2rtc.

    Retorna o processo do go2rtc (ou None se ja estava rodando).
    Encerra o programa se o go2rtc nao subir.
    """
    for arq in [GO2RTC_EXE, GO2RTC_CONFIG]:
        if not os.path.exists(arq):
            log(f"[ERRO] Arquivo nao encontrado: {arq}", RED)
            input("\nPressione ENTER para sair...")
            sys.exit(1)

    # ── Descoberta dinamica do IP da camera ──
    ip_camera, mac_camera = descobrir_ip_camera(config_cam)

    if ip_camera:
        # Persiste o novo IP e MAC para a proxima execucao
        config_cam["ultimo_ip"] = ip_camera
        if mac_camera:
            config_cam["mac"] = mac_camera
        salvar_config_camera(config_cam)

        # Atualiza go2rtc.yaml com o IP atual
        atualizar_ip_go2rtc(GO2RTC_CONFIG, ip_camera)
    else:
        log("[AVISO] Continuando sem atualizar o IP. A camera pode nao conectar.", YELLOW)

    # ── Verifica se go2rtc ja esta rodando ──
    if porta_aberta(RTSP_HOST, RTSP_PORT):
        log("[INFO] go2rtc ja esta rodando (porta 8554 ativa).", CYAN)
        return None

    # ── Inicia go2rtc em janela separada ──
    log(f"[>>]  Iniciando {GO2RTC_EXE}...", CYAN)
    go2rtc_proc = subprocess.Popen(
        [GO2RTC_EXE, "-config", GO2RTC_CONFIG],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        cwd=script_dir
    )

    # ── Aguarda go2rtc ficar pronto ──
    if not aguardar_go2rtc():
        log(f"[ERRO] go2rtc nao respondeu em {WAIT_TIMEOUT}s. Verifique o go2rtc.yaml.", RED)
        go2rtc_proc.terminate()
        input("\nPressione ENTER para sair...")
        sys.exit(1)
    return go2rtc_proc


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # ── 0. Limpa instâncias anteriores órfãs do go2rtc.exe e de replay_cam.py ──
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "go2rtc.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = proc.info['cmdline']
            if cmd and any('replay_cam.py' in part for part in cmd):
                if proc.pid != os.getpid():
                    log(f"[INFO] Finalizando instância órfã de replay_cam.py (PID {proc.pid})...", YELLOW)
                    proc.terminate()
                    proc.wait(timeout=2)
        except Exception:
            pass

    print(f"""
{CYAN}{BOLD}=======================================================
  LAUNCHER - Replay System
======================================================={RESET}
""")

    # ── 1. Verifica arquivos necessarios ──
    if not os.path.exists(REPLAY_SCRIPT):
        log(f"[ERRO] Arquivo nao encontrado: {REPLAY_SCRIPT}", RED)
        input("\nPressione ENTER para sair...")
        sys.exit(1)

    # ── 2. Usuario escolhe a camera ──
    config_cam = carregar_config_camera()
    nome_camera, indice = escolher_camera(config_cam)

    # ── 3. Prepara a fonte escolhida ──
    go2rtc_proc = None
    if indice is None:
        # Camera IP: descobre IP + sobe go2rtc (RTSP local)
        go2rtc_proc = preparar_xiaomi(config_cam, script_dir)
        fonte = RTSP_URL
    else:
        # Camera local: abre o app Iriun se for o celular
        if cameras.eh_iriun(nome_camera):
            cameras.garantir_iriun(log=lambda m: log(m, CYAN))
        fonte = str(indice)

    # ── 4. Inicia replay_cam.py ──
    log("[>>]  Iniciando sistema de replay...\n", GREEN)

    replay_proc = subprocess.Popen(
        [sys.executable, REPLAY_SCRIPT, "--camera", fonte, "--nome", nome_camera],
        cwd=script_dir
    )

    try:
        replay_proc.wait()
    except KeyboardInterrupt:
        log("\nEncerrando...", YELLOW)
        replay_proc.terminate()

    # ── 5. Encerra go2rtc junto (se foi iniciado aqui) ──
    if go2rtc_proc and go2rtc_proc.poll() is None:
        log("[>>]  Encerrando go2rtc...", YELLOW)
        go2rtc_proc.terminate()
        try:
            go2rtc_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            go2rtc_proc.kill()

    log("Sistema encerrado com sucesso.", CYAN)


if __name__ == "__main__":
    main()
