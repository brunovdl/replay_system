/*
 * Replay Quadra — página de filmagem (Chrome Android)
 *
 * Fluxo:
 *   câmera ──> MediaStreamTrackProcessor ──> VideoEncoder (H.264, hardware)
 *          ──> buffer circular dos últimos REPLAY_SECONDS (pedaços já comprimidos)
 *   [REPLAY] ──> junta os pedaços em um H.264 cru ──> POST /api/replay
 *
 * O buffer sempre começa num keyframe (pedido a cada KEYFRAME_SECS), então
 * qualquer recorte dele é um vídeo decodificável sozinho.
 */
"use strict";

const CFG = {
  REPLAY_SECONDS: 30,
  WIDTH: 1280,
  HEIGHT: 720,
  FPS: 30,
  BITRATE: 2_500_000,     // 2,5 Mbps -> clipe de 30s ~= 9 MB (envio rápido no 4G)
  KEYFRAME_SECS: 1,       // define a precisão do corte no início do clipe
  DEBOUNCE_MS: 2000,      // ignora toques repetidos no botão
  MAX_ENCODE_QUEUE: 4,    // acima disso o celular não está dando conta: descarta frame
  TELEMETRIA_SECS: 60,    // envia bateria/fps ao servidor (para avaliar o teste de campo)
};

// Perfis H.264 em ordem de preferência (todos suportam 1280x720 @ 30fps)
const CODECS = ["avc1.4D401F", "avc1.42E01F", "avc1.640028"];

const $ = (id) => document.getElementById(id);
const estado = {
  token: "",
  stream: null,
  encoder: null,
  codec: "",
  largura: 0,
  altura: 0,
  chunks: [],          // { data: Uint8Array, ts: µs, key: bool }
  bytes: 0,
  paramSets: null,     // SPS+PPS (Annex B), caso o encoder só mande no 1º keyframe
  ultimoKeyTs: -Infinity,
  framesSeg: 0,
  fps: 0,
  descartados: 0,
  inicio: 0,
  ultimoDisparo: 0,
  audio: null,
  wakeLock: null,
  bateria: null,
  deviceId: "",        // câmera em uso
  reader: null,        // leitor de frames do track atual
  geracao: 0,          // incrementa a cada troca de câmera (encerra o loop antigo)
};

// ─────────────────────────────────────────────
//  Utilidades
// ─────────────────────────────────────────────
function aviso(texto, ms = 2500) {
  const el = $("aviso");
  el.textContent = texto;
  el.classList.remove("hidden");
  clearTimeout(aviso._t);
  if (ms) aviso._t = setTimeout(() => el.classList.add("hidden"), ms);
}

function bipe(sequencia) {
  // sequencia = [[freq_hz, dur_ms], ...]
  const ctx = estado.audio;
  if (!ctx) return;
  let t = ctx.currentTime;
  for (const [freq, dur] of sequencia) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.3, t);
    gain.gain.exponentialRampToValueAtTime(0.001, t + dur / 1000);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + dur / 1000);
    t += dur / 1000 + 0.05;
  }
}

function api(caminho, opcoes = {}) {
  const headers = { ...(opcoes.headers || {}), "X-Replay-Token": estado.token };
  return fetch(caminho, { ...opcoes, headers });
}

function formatarTempo(seg) {
  const h = Math.floor(seg / 3600), m = Math.floor(seg / 60) % 60, s = Math.floor(seg % 60);
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(s).padStart(2, "0")}`;
}

// ─────────────────────────────────────────────
//  H.264 Annex B: localizar SPS/PPS
// ─────────────────────────────────────────────
/** Divide um buffer Annex B em NALs: [{ tipo, inicio, fim }] (inicio inclui o start code). */
function nals(data) {
  const lista = [];
  let i = 0, inicioAtual = -1, tipoAtual = 0;
  while (i + 3 <= data.length) {
    let sc = 0;
    if (data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 1) sc = 3;
    else if (i + 4 <= data.length && data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 0 && data[i + 3] === 1) sc = 4;
    if (sc) {
      if (inicioAtual >= 0) lista.push({ tipo: tipoAtual, inicio: inicioAtual, fim: i });
      inicioAtual = i;
      tipoAtual = data[i + sc] & 0x1f;
      i += sc + 1;
    } else {
      i++;
    }
  }
  if (inicioAtual >= 0) lista.push({ tipo: tipoAtual, inicio: inicioAtual, fim: data.length });
  return lista;
}

function temSps(data) {
  return nals(data).some((n) => n.tipo === 7);
}

/** Guarda SPS (7) e PPS (8) do keyframe para colar no início do clipe se faltar. */
function guardarParamSets(data) {
  const partes = nals(data).filter((n) => n.tipo === 7 || n.tipo === 8);
  if (!partes.length) return;
  const total = partes.reduce((s, n) => s + n.fim - n.inicio, 0);
  const out = new Uint8Array(total);
  let pos = 0;
  for (const n of partes) {
    out.set(data.subarray(n.inicio, n.fim), pos);
    pos += n.fim - n.inicio;
  }
  estado.paramSets = out;
}

// ─────────────────────────────────────────────
//  Buffer circular de pedaços comprimidos
// ─────────────────────────────────────────────
function aoCodificar(chunk) {
  const data = new Uint8Array(chunk.byteLength);
  chunk.copyTo(data);
  const key = chunk.type === "key";
  if (key && temSps(data)) guardarParamSets(data);

  const lista = estado.chunks;
  lista.push({ data, ts: chunk.timestamp, key });
  estado.bytes += data.byteLength;
  estado.framesSeg++;

  // Remove do início em blocos de GOP inteiros: corta até o próximo keyframe
  // sempre que ele já for mais antigo que a janela. Assim o buffer começa num
  // keyframe e cobre pelo menos REPLAY_SECONDS.
  const corte = chunk.timestamp - CFG.REPLAY_SECONDS * 1e6;
  for (;;) {
    let k = 1;
    while (k < lista.length && !lista[k].key) k++;
    if (k >= lista.length || lista[k].ts > corte) break;
    for (let i = 0; i < k; i++) estado.bytes -= lista[i].data.byteLength;
    lista.splice(0, k);
  }
}

async function configurarEncoder(largura, altura) {
  const base = {
    width: largura,
    height: altura,
    bitrate: CFG.BITRATE,
    framerate: CFG.FPS,
    latencyMode: "realtime",
    avc: { format: "annexb" },   // H.264 cru com start codes: o servidor lê direto
  };
  for (const accel of ["prefer-hardware", "no-preference"]) {
    for (const codec of CODECS) {
      const config = { ...base, codec, hardwareAcceleration: accel };
      try {
        const { supported } = await VideoEncoder.isConfigSupported(config);
        if (!supported) continue;
        if (estado.encoder && estado.encoder.state !== "closed") estado.encoder.close();
        estado.encoder = new VideoEncoder({
          output: aoCodificar,
          error: (e) => {
            console.error(e);
            aviso("Erro no codificador — reiniciando", 3000);
            estado.encoder = null;   // o próximo frame reconfigura
          },
        });
        estado.encoder.configure(config);
        estado.codec = `${codec}${accel === "prefer-hardware" ? " (hw)" : ""}`;
        if (largura !== estado.largura || altura !== estado.altura) {
          // Tamanho mudou (ex.: girou o celular): pedaços antigos não servem mais.
          // Num reinício após erro (mesmo tamanho) o buffer é mantido.
          estado.chunks = [];
          estado.bytes = 0;
          estado.paramSets = null;
        }
        estado.largura = largura;
        estado.altura = altura;
        estado.ultimoKeyTs = -Infinity;   // encoder novo precisa começar com keyframe
        return;
      } catch (e) {
        console.warn("config recusada", config, e);
      }
    }
  }
  throw new Error("Este celular não consegue codificar H.264 nesta resolução.");
}

// ─────────────────────────────────────────────
//  Captura
// ─────────────────────────────────────────────
async function lerFrames(track, geracao) {
  const reader = new MediaStreamTrackProcessor({ track }).readable.getReader();
  estado.reader = reader;
  let configurando = null;
  for (;;) {
    const { value: frame, done } = await reader.read();
    if (done) break;
    if (geracao !== estado.geracao) {   // câmera trocada: este loop é de um track antigo
      frame.close();
      break;
    }
    try {
      const w = frame.displayWidth, h = frame.displayHeight;
      if (!estado.encoder || w !== estado.largura || h !== estado.altura) {
        configurando ??= configurarEncoder(w, h).finally(() => (configurando = null));
        await configurando;
      }
      const enc = estado.encoder;
      if (!enc || enc.state !== "configured") continue;
      if (enc.encodeQueueSize > CFG.MAX_ENCODE_QUEUE) {
        estado.descartados++;
        continue;
      }
      const keyFrame = frame.timestamp - estado.ultimoKeyTs >= CFG.KEYFRAME_SECS * 1e6;
      if (keyFrame) estado.ultimoKeyTs = frame.timestamp;
      enc.encode(frame, { keyFrame });
    } catch (e) {
      console.error(e);
      aviso(e.message || String(e), 5000);
    } finally {
      frame.close();   // obrigatório: sem isso a câmera trava em poucos frames
    }
  }
}

async function pedirWakeLock() {
  try {
    estado.wakeLock = await navigator.wakeLock.request("screen");
  } catch (e) {
    console.warn("wakeLock", e);
  }
}

async function abrirStream(deviceId) {
  const video = {
    width: { ideal: CFG.WIDTH },
    height: { ideal: CFG.HEIGHT },
    frameRate: { ideal: CFG.FPS },
  };
  if (deviceId) video.deviceId = { exact: deviceId };
  else video.facingMode = { ideal: "environment" };
  return navigator.mediaDevices.getUserMedia({ audio: false, video });
}

/** Abre a câmera (a salva na última vez, ou a traseira) e começa a gravar o buffer. */
async function iniciarCamera(deviceId = lerPreferencia()) {
  let stream;
  try {
    stream = await abrirStream(deviceId);
  } catch (e) {
    // Câmera salva não existe mais (ou foi recusada): volta para a traseira padrão
    if (!deviceId || e.name === "NotAllowedError") throw e;
    stream = await abrirStream(null);
  }
  estado.stream = stream;
  const track = stream.getVideoTracks()[0];
  estado.deviceId = track.getSettings().deviceId || "";
  const frontal = (track.getSettings().facingMode || "") === "user";
  const preview = $("preview");
  preview.srcObject = stream;
  // Espelha só o preview da frontal (como um espelho); o vídeo gravado não é espelhado
  preview.style.transform = frontal ? "scaleX(-1)" : "";

  track.addEventListener("ended", () => aviso("A câmera foi desligada. Recarregue a página.", 0));
  track.addEventListener("mute", () => aviso("Câmera pausada — mantenha esta tela aberta", 0));
  track.addEventListener("unmute", () => $("aviso").classList.add("hidden"));

  const geracao = ++estado.geracao;
  lerFrames(track, geracao).catch((e) => {
    if (geracao === estado.geracao) aviso("Falha na captura: " + e.message, 0);
  });
}

// ─────────────────────────────────────────────
//  Troca de câmera
// ─────────────────────────────────────────────
function lerPreferencia() {
  try { return localStorage.getItem("replay_camera") || null; } catch { return null; }
}

function salvarPreferencia(deviceId) {
  try { localStorage.setItem("replay_camera", deviceId); } catch {}
}

/** Lista as câmeras com nomes legíveis. Os nomes reais só aparecem depois da permissão. */
async function listarCameras() {
  const dispositivos = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  const contagem = { traseira: 0, frontal: 0, outra: 0 };
  return dispositivos.map((d) => {
    const rotulo = d.label.toLowerCase();
    const tipo = /front|user|frontal/.test(rotulo) ? "frontal" : /back|rear|environment|traseira/.test(rotulo) ? "traseira" : "outra";
    contagem[tipo]++;
    return { deviceId: d.deviceId, label: d.label, tipo, numero: contagem[tipo] };
  }).map((c) => {
    const base = { traseira: "Traseira", frontal: "Frontal", outra: "Câmera" }[c.tipo];
    // Numera só quando há mais de uma do mesmo tipo (ex.: traseira principal + grande-angular)
    return { ...c, nome: contagem[c.tipo] > 1 ? `${base} ${c.numero}` : base };
  });
}

async function abrirSeletorCameras() {
  const lista = $("lista-cameras");
  lista.innerHTML = "";
  let cameras = [];
  try {
    cameras = await listarCameras();
  } catch (e) {
    aviso("Não foi possível listar as câmeras");
    return;
  }
  for (const cam of cameras) {
    const btn = document.createElement("button");
    btn.className = "opcao-camera" + (cam.deviceId === estado.deviceId ? " atual" : "");
    btn.innerHTML = `<b></b><small></small>`;
    btn.querySelector("b").textContent = cam.nome + (cam.deviceId === estado.deviceId ? " ✓" : "");
    btn.querySelector("small").textContent = cam.label || "sem nome";
    btn.addEventListener("click", () => {
      fecharSeletorCameras();
      if (cam.deviceId !== estado.deviceId) trocarCamera(cam.deviceId, cam.nome);
    });
    lista.appendChild(btn);
  }
  if (cameras.length <= 1) {
    const p = document.createElement("p");
    p.textContent = "O navegador só expõe esta câmera neste celular.";
    lista.appendChild(p);
  }
  $("seletor-cameras").classList.remove("hidden");
}

function fecharSeletorCameras() {
  $("seletor-cameras").classList.add("hidden");
}

async function trocarCamera(deviceId, nome) {
  const btnReplay = $("btn-replay");
  btnReplay.disabled = true;
  aviso(`Trocando para ${nome}…`, 0);
  try {
    // Encerra o track e o loop antigos antes de abrir o novo: muitos celulares
    // não abrem duas câmeras ao mesmo tempo
    estado.geracao++;
    try { await estado.reader?.cancel(); } catch {}
    estado.stream?.getTracks().forEach((t) => t.stop());
    if (estado.encoder && estado.encoder.state !== "closed") estado.encoder.close();
    estado.encoder = null;
    // Buffer recomeça: trechos de câmeras diferentes não formam um vídeo válido
    estado.chunks = [];
    estado.bytes = 0;
    estado.paramSets = null;
    estado.largura = 0;
    estado.altura = 0;

    await iniciarCamera(deviceId);
    salvarPreferencia(estado.deviceId);
    aviso(`${nome} — buffer reiniciado`, 2500);
  } catch (e) {
    console.error(e);
    aviso("Não foi possível abrir essa câmera. Voltando para a traseira…", 3000);
    try { await iniciarCamera(null); } catch (e2) { aviso("Falha ao reabrir a câmera: " + e2.message, 0); }
  } finally {
    btnReplay.disabled = false;
  }
}

// ─────────────────────────────────────────────
//  Replay: recortar, enviar, acompanhar
// ─────────────────────────────────────────────
function montarClipe() {
  const pedacos = estado.chunks.slice();
  if (pedacos.length < 2 || !pedacos[0].key) return null;
  const duracao = (pedacos[pedacos.length - 1].ts - pedacos[0].ts) / 1e6;
  if (duracao < 1) return null;
  const partes = pedacos.map((p) => p.data);
  if (!temSps(partes[0]) && estado.paramSets) partes.unshift(estado.paramSets);
  return {
    blob: new Blob(partes, { type: "video/h264" }),
    meta: {
      fps: (pedacos.length - 1) / duracao,   // fps real do trecho (define a velocidade no MP4)
      duracao,
      frames: pedacos.length,
      largura: estado.largura,
      altura: estado.altura,
      codec: estado.codec,
      evento: "botao",
      disparado_em: Date.now(),
    },
  };
}

function itemEnvio() {
  const el = document.createElement("div");
  el.className = "envio andamento";
  $("envios").prepend(el);
  // Mantém só os 4 mais recentes na tela
  while ($("envios").children.length > 4) $("envios").lastElementChild.remove();
  return el;
}

async function enviarClipe(clipe, el, tentativa = 1) {
  const hora = new Date(clipe.meta.disparado_em).toLocaleTimeString("pt-BR");
  const mb = (clipe.blob.size / 1e6).toFixed(1);
  el.className = "envio andamento";
  el.textContent = `${hora} · enviando ${mb} MB${tentativa > 1 ? ` (tentativa ${tentativa})` : ""}…`;
  try {
    const form = new FormData();
    form.append("file", clipe.blob, "clipe.h264");
    form.append("meta", JSON.stringify(clipe.meta));
    const resp = await api("/api/replay", { method: "POST", body: form });
    if (resp.status === 401) throw Object.assign(new Error("senha inválida"), { definitivo: true });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const { id } = await resp.json();
    acompanhar(id, el, hora);
  } catch (e) {
    if (e.definitivo || tentativa >= 5) {
      el.className = "envio erro";
      el.textContent = `${hora} · falhou: ${e.message}`;
      return;
    }
    // Sem sinal no 4G: tenta de novo em 5s, 10s, 20s, 40s (o clipe fica na memória)
    const espera = 5000 * 2 ** (tentativa - 1);
    el.textContent = `${hora} · sem conexão, nova tentativa em ${espera / 1000}s`;
    setTimeout(() => enviarClipe(clipe, el, tentativa + 1), espera);
  }
}

async function acompanhar(id, el, hora) {
  const textos = {
    recebido: "recebido pelo servidor",
    convertendo: "gerando vídeo…",
    enviando: "enviando ao WhatsApp…",
  };
  for (let i = 0; i < 90; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      const st = await (await api(`/api/replay/${id}`)).json();
      if (st.estado === "concluido") {
        el.className = "envio ok";
        el.textContent = `${hora} · ✔ ${st.mensagem}`;
        return;
      }
      if (st.estado === "erro") {
        el.className = "envio erro";
        el.textContent = `${hora} · erro no servidor: ${st.mensagem}`;
        return;
      }
      el.textContent = `${hora} · ${textos[st.estado] || st.estado}`;
    } catch {
      /* sem sinal momentâneo: continua consultando */
    }
  }
}

function dispararReplay(origem = "botao") {
  const agora = Date.now();
  if (agora - estado.ultimoDisparo < CFG.DEBOUNCE_MS) return;
  const clipe = montarClipe();
  if (!clipe) {
    aviso("Aguarde o buffer encher");
    return;
  }
  estado.ultimoDisparo = agora;
  clipe.meta.evento = origem;

  navigator.vibrate?.([120, 60, 120]);
  bipe([[1400, 150], [1800, 300]]);
  const flash = $("flash");
  flash.classList.add("on");
  setTimeout(() => flash.classList.remove("on"), 400);
  aviso(`REPLAY! ${clipe.meta.duracao.toFixed(0)}s`, 1500);

  enviarClipe(clipe, itemEnvio());
}

// ─────────────────────────────────────────────
//  Painel de status + telemetria do teste de campo
// ─────────────────────────────────────────────
function atualizarPainel() {
  estado.fps = estado.framesSeg;
  estado.framesSeg = 0;
  const c = estado.chunks;
  const seg = c.length > 1 ? (c[c.length - 1].ts - c[0].ts) / 1e6 : 0;
  $("st-buffer").textContent = Math.min(seg, 99).toFixed(0);
  $("buffer-nivel").style.width = `${Math.min(100, (seg / CFG.REPLAY_SECONDS) * 100)}%`;
  $("st-fps").textContent = estado.fps;
  $("st-res").textContent = estado.largura ? `${estado.largura}x${estado.altura}` : "—";
  $("st-mem").textContent = (estado.bytes / 1e6).toFixed(1);
  $("st-tempo").textContent = formatarTempo((Date.now() - estado.inicio) / 1000);
  if (estado.descartados) {
    $("st-descartes").classList.remove("hidden");
    $("st-desc").textContent = estado.descartados;
  }
  const b = estado.bateria;
  if (b) $("st-bat").textContent = `${Math.round(b.level * 100)}%${b.charging ? " ⚡" : ""}`;
}

function enviarTelemetria() {
  const b = estado.bateria;
  const dados = {
    minutos: (Date.now() - estado.inicio) / 60000,
    fps: estado.fps,
    bateria: b ? Math.round(b.level * 100) : null,
    carregando: b ? b.charging : null,
    descartados: estado.descartados,
    codec: estado.codec,
    resolucao: `${estado.largura}x${estado.altura}`,
    buffer_mb: estado.bytes / 1e6,
  };
  api("/api/telemetria", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dados),
  }).catch(() => {});
}

// ─────────────────────────────────────────────
//  Início
// ─────────────────────────────────────────────
function checarCompatibilidade() {
  const faltando = [];
  if (!window.isSecureContext) faltando.push("HTTPS");
  if (!("VideoEncoder" in window)) faltando.push("VideoEncoder (WebCodecs)");
  if (!("MediaStreamTrackProcessor" in window)) faltando.push("MediaStreamTrackProcessor");
  if (!navigator.mediaDevices?.getUserMedia) faltando.push("acesso à câmera");
  return faltando;
}

async function comecar() {
  const btn = $("btn-comecar");
  const erro = $("erro-inicio");
  erro.textContent = "";
  btn.disabled = true;
  try {
    const faltando = checarCompatibilidade();
    if (faltando.length) throw new Error("Navegador sem suporte a: " + faltando.join(", ") + ". Use o Chrome no Android.");

    estado.token = $("senha").value.trim();
    const login = await api("/api/login", { method: "POST" });
    if (login.status === 401) throw new Error("Senha incorreta.");
    if (!login.ok) throw new Error(`Servidor respondeu ${login.status}.`);
    try { localStorage.setItem("replay_token", estado.token); } catch {}

    // Precisam de um toque do usuário: áudio, tela cheia, tela sempre ligada
    estado.audio = new (window.AudioContext || window.webkitAudioContext)();
    try {
      await document.documentElement.requestFullscreen();
      await screen.orientation.lock("landscape");
    } catch { /* nem todo aparelho permite; segue em frente */ }
    await pedirWakeLock();
    estado.bateria = await navigator.getBattery?.().catch(() => null);

    await iniciarCamera();

    $("inicio").classList.add("hidden");
    $("filmagem").classList.remove("hidden");
    estado.inicio = Date.now();
    setInterval(atualizarPainel, 1000);
    setInterval(enviarTelemetria, CFG.TELEMETRIA_SECS * 1000);
    bipe([[900, 120]]);
  } catch (e) {
    erro.textContent = e.name === "NotAllowedError" ? "Permita o acesso à câmera para continuar." : e.message;
    btn.disabled = false;
  }
}

document.addEventListener("visibilitychange", () => {
  // O Chrome solta o wake lock quando a página some; pede de novo ao voltar
  if (document.visibilityState === "visible" && estado.stream) pedirWakeLock();
});

$("btn-comecar").addEventListener("click", comecar);
$("btn-replay").addEventListener("click", () => dispararReplay("botao"));
$("btn-camera").addEventListener("click", abrirSeletorCameras);
$("btn-fechar-cameras").addEventListener("click", fecharSeletorCameras);
$("senha").addEventListener("keydown", (e) => e.key === "Enter" && comecar());
try { $("senha").value = localStorage.getItem("replay_token") || ""; } catch {}
