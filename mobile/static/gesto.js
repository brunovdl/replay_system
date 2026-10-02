/*
 * Gatilho por gesto: braço levantado por GESTO.HOLD_SECS dispara o replay.
 *
 * MediaPipe Pose Landmarker rodando no próprio celular (GPU), ~5 vezes por
 * segundo, sobre o <video> do preview. Mesma regra e mesma máquina de estados
 * do replay_cam.py (versão PC), já testadas lá.
 */
"use strict";

const GESTO = {
  HOLD_SECS: 3,          // tempo com o braço levantado para disparar (PC usa 4s)
  GRACE_SECS: 1,         // falhas momentâneas de detecção não cancelam o hold
  COOLDOWN_SECS: 5,      // depois de disparar, espera isso antes de aceitar outro gesto
  FPS: 5,                // detecções por segundo (economiza bateria e evita aquecer)
  VISIBILIDADE: 0.5,     // confiança mínima de cada ponto do corpo (0-1)
  NUM_POSES: 6,          // jogadores analisados por frame
  VERSAO: "1.0.1",
  MODELO: "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
};
GESTO.BUNDLE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${GESTO.VERSAO}/vision_bundle.mjs`;
GESTO.WASM = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${GESTO.VERSAO}/wasm`;

// Índices dos pontos no MediaPipe Pose (33 pontos)
const P = {
  CABECA: [0, 1, 2, 3, 4, 5, 6, 7, 8],  // nariz, olhos, orelhas
  OMBRO_E: 11, OMBRO_D: 12,
  COTOVELO_E: 13, COTOVELO_D: 14,
  PULSO_E: 15, PULSO_D: 16,
};

/**
 * Decide se UMA pessoa está com o braço levantado. lm = 33 pontos normalizados
 * ({x, y, visibility}; y menor = mais alto na imagem).
 *
 * Referência de altura, em ordem de preferência:
 *   1. Linha da cabeça = ponto mais baixo entre nariz, olhos e orelhas visíveis
 *      (~altura do nariz). Vários pontos, e não só o nariz, para funcionar com
 *      o jogador de costas.
 *   2. Sem cabeça visível: o ombro do mesmo lado, com margem de meia largura
 *      de ombros (o pulso precisa estar bem acima do ombro).
 * O cotovelo acima da cabeça também conta: o pulso some com o borrão do movimento.
 */
function bracoLevantado(lm, vis = GESTO.VISIBILIDADE) {
  const ok = (i) => lm[i] && lm[i].visibility > vis;
  const cabecaYs = P.CABECA.filter(ok).map((i) => lm[i].y);
  const linhaCabeca = cabecaYs.length ? Math.max(...cabecaYs) : null;

  const ombrosOk = ok(P.OMBRO_E) && ok(P.OMBRO_D);
  const margem = ombrosOk ? Math.abs(lm[P.OMBRO_E].x - lm[P.OMBRO_D].x) * 0.5 : 0;

  for (const [pulso, cotovelo, ombro] of [
    [P.PULSO_E, P.COTOVELO_E, P.OMBRO_E],
    [P.PULSO_D, P.COTOVELO_D, P.OMBRO_D],
  ]) {
    if (linhaCabeca !== null) {
      if (ok(pulso) && lm[pulso].y < linhaCabeca) return true;
      if (ok(cotovelo) && lm[cotovelo].y < linhaCabeca) return true;
    } else if (ok(pulso) && ok(ombro) && margem > 0) {
      if (lm[pulso].y < lm[ombro].y - margem) return true;
    }
  }
  return false;
}

/** Máquina de estados do hold (mesma do PC). Tempos em segundos. */
class MaquinaHold {
  constructor({ aoIniciar = () => {}, aoDisparar = () => {}, aoCancelar = () => {} } = {}) {
    this.aoIniciar = aoIniciar;
    this.aoDisparar = aoDisparar;
    this.aoCancelar = aoCancelar;
    this.inicioHold = 0;      // 0 = sem hold em andamento
    this.vistoEm = 0;         // última vez que um braço levantado foi visto
    this.disparou = false;    // true até o braço abaixar (não repete com o braço parado no alto)
    this.cooldownAte = 0;
  }

  atualizar(gestoOk, agora) {
    if (gestoOk) {
      this.vistoEm = agora;
      if (!this.disparou && agora >= this.cooldownAte) {
        if (this.inicioHold === 0) {
          this.inicioHold = agora;
          this.aoIniciar();
        }
        if (agora - this.inicioHold >= GESTO.HOLD_SECS) {
          this.disparou = true;
          this.inicioHold = 0;
          this.cooldownAte = agora + GESTO.COOLDOWN_SECS;
          this.aoDisparar();
        }
      }
    } else if (agora - this.vistoEm > GESTO.GRACE_SECS) {
      // Braço sumiu por mais que a tolerância: cancela o hold e rearma o gatilho
      if (this.inicioHold > 0 && !this.disparou) this.aoCancelar();
      this.inicioHold = 0;
      this.disparou = false;
    }
  }

  /** Progresso do hold atual (0-1), ou null se não há hold. */
  progresso(agora) {
    if (this.inicioHold === 0) return null;
    return Math.min(1, (agora - this.inicioHold) / GESTO.HOLD_SECS);
  }

  restante(agora) {
    return Math.max(0, GESTO.HOLD_SECS - (agora - this.inicioHold));
  }
}

/** Baixa o MediaPipe (~18 MB na 1ª vez; depois fica no cache) e cria o detector. */
async function carregarDetectorPose() {
  const vision = await import(GESTO.BUNDLE);
  const fileset = await vision.FilesetResolver.forVisionTasks(GESTO.WASM);
  const opcoes = (delegate) => ({
    baseOptions: { modelAssetPath: GESTO.MODELO, delegate },
    runningMode: "VIDEO",
    numPoses: GESTO.NUM_POSES,
    minPoseDetectionConfidence: 0.4,
    minPosePresenceConfidence: 0.4,
    minTrackingConfidence: 0.4,
  });
  try {
    return { detector: await vision.PoseLandmarker.createFromOptions(fileset, opcoes("GPU")), delegate: "GPU" };
  } catch (e) {
    console.warn("GPU indisponível para o MediaPipe, usando CPU", e);
    return { detector: await vision.PoseLandmarker.createFromOptions(fileset, opcoes("CPU")), delegate: "CPU" };
  }
}

/**
 * Roda a detecção em loop (GESTO.FPS por segundo) sobre o <video>.
 * deveRodar() permite pausar (gesto desligado) sem destruir o detector.
 */
function iniciarLoopGesto(video, detector, maquina, { deveRodar, aoResultado = () => {} }) {
  const intervalo = 1000 / GESTO.FPS;
  let ultimoTs = 0;
  async function passo() {
    const t0 = performance.now();
    try {
      if (deveRodar() && video.readyState >= 2 && video.videoWidth > 0) {
        const ts = Math.max(t0, ultimoTs + 1);   // MediaPipe exige timestamps crescentes
        ultimoTs = ts;
        const res = detector.detectForVideo(video, ts);
        const poses = res.landmarks || [];
        const levantados = poses.filter((lm) => bracoLevantado(lm)).length;
        maquina.atualizar(levantados > 0, ts / 1000);
        aoResultado({ pessoas: poses.length, levantados, ms: performance.now() - t0 });
      }
    } catch (e) {
      console.error("detecção de pose", e);
    }
    setTimeout(passo, Math.max(0, intervalo - (performance.now() - t0)));
  }
  passo();
}
