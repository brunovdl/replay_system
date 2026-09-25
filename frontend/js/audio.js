/**
 * Síntese de áudio via Web Audio API e vibração háptica
 * Não depende de arquivos externos de áudio (funciona 100% offline).
 */

let audioCtx = null;

function getAudioContext() {
  if (!audioCtx) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      audioCtx = new AudioContextClass();
    }
  }
  if (audioCtx && audioCtx.state === "suspended") {
    audioCtx.resume();
  }
  return audioCtx;
}

/**
 * Toca apito duplo de árbitro para confirmação de replay capturado
 */
export function playReplayWhistle(soundEnabled = true, vibrationEnabled = true) {
  // Vibração háptica no smartphone
  if (vibrationEnabled && navigator.vibrate) {
    try {
      navigator.vibrate([250, 100, 250, 100, 400]);
    } catch (e) {
      console.warn("Vibration API indisponível:", e);
    }
  }

  if (!soundEnabled) return;

  try {
    const ctx = getAudioContext();
    if (!ctx) return;

    const now = ctx.currentTime;

    // Primeiro apito curto
    createWhistleTone(ctx, now, 0.18, 1800, 2400);

    // Segundo apito longo e forte
    createWhistleTone(ctx, now + 0.25, 0.45, 2000, 2800);

  } catch (e) {
    console.warn("Erro ao sintetizar áudio de apito:", e);
  }
}

function createWhistleTone(ctx, startTime, duration, startFreq, endFreq) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();

  // Tipo de onda triangular/senoidal dá o timbre agudo de apito
  osc.type = "sine";
  osc.frequency.setValueAtTime(startFreq, startTime);
  osc.frequency.linearRampToValueAtTime(endFreq, startTime + duration * 0.7);
  osc.frequency.linearRampToValueAtTime(startFreq, startTime + duration);

  // Envelope ADSR
  gain.gain.setValueAtTime(0, startTime);
  gain.gain.linearRampToValueAtTime(0.7, startTime + 0.03);
  gain.gain.setValueAtTime(0.7, startTime + duration - 0.05);
  gain.gain.linearRampToValueAtTime(0, startTime + duration);

  osc.connect(gain);
  gain.connect(ctx.destination);

  osc.start(startTime);
  osc.stop(startTime + duration);
}

/**
 * Beep suave para contagem regressiva (3, 2, 1)
 */
export function playCountdownBeep(soundEnabled = true) {
  if (!soundEnabled) return;
  try {
    const ctx = getAudioContext();
    if (!ctx) return;

    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = "sine";
    osc.frequency.setValueAtTime(880, now); // Nota Lá (A5)

    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(0.3, now + 0.02);
    gain.gain.linearRampToValueAtTime(0, now + 0.1);

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start(now);
    osc.stop(now + 0.1);
  } catch (e) {
    console.warn("Erro no beep de contagem:", e);
  }
}
