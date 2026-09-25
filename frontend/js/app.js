/**
 * Aplicação Principal Replay 2.0 (Mobile Web)
 * Orquestra câmera, buffer circular, MediaPipe, HUD e comunicação com backend.
 */

import { getConfig, saveConfig } from "./config.js";
import { playReplayWhistle, playCountdownBeep } from "./audio.js";
import { VideoBufferRecorder } from "./recorder.js";
import { PoseTriggerDetector } from "./pose.js";

// Elementos do DOM
const videoElement = document.getElementById("camera-viewfinder");
const bufferStatusBadge = document.getElementById("buffer-status-badge");
const bufferTimeText = document.getElementById("buffer-time-text");
const aiStatusBadge = document.getElementById("ai-status-badge");
const backendStatusBadge = document.getElementById("backend-status-badge");

const triggerContainer = document.getElementById("trigger-container");
const progressRingBar = document.getElementById("progress-ring-bar");
const triggerCounterText = document.getElementById("trigger-counter-text");
const cooldownPill = document.getElementById("cooldown-pill");
const cooldownSecondsText = document.getElementById("cooldown-seconds-text");

const btnManualReplay = document.getElementById("btn-manual-replay");
const btnSettings = document.getElementById("btn-settings");
const btnFullscreen = document.getElementById("btn-fullscreen");
const btnSwitchCamera = document.getElementById("btn-switch-camera");
const btnQrMobile = document.getElementById("btn-qr-mobile");
const flashEffect = document.getElementById("flash-effect");
const toastNotification = document.getElementById("toast-notification");
const toastMessage = document.getElementById("toast-message");

// Banner de Contexto Inseguro
const insecureBanner = document.getElementById("insecure-context-banner");
const btnGoHttps = document.getElementById("btn-go-https");

// Modal de Configurações
const modalSettings = document.getElementById("modal-settings");
const btnCloseModal = document.getElementById("btn-close-modal");
const btnSaveSettings = document.getElementById("btn-save-settings");
const btnTestBackend = document.getElementById("btn-test-backend");
const selectCamera = document.getElementById("setting-camera-select");

// Modal de QR Code / Conectar Celular
const modalQr = document.getElementById("modal-qr");
const btnCloseQrModal = document.getElementById("btn-close-qr-modal");
const qrLoadingText = document.getElementById("qr-loading-text");
const qrImage = document.getElementById("qr-image");
const qrLinkAnchor = document.getElementById("qr-link-anchor");
const btnCopyQrLink = document.getElementById("btn-copy-qr-link");
const btnRefreshTunnel = document.getElementById("btn-refresh-tunnel");

const inputBackendUrl = document.getElementById("setting-backend-url");
const selectBufferSeconds = document.getElementById("setting-buffer-seconds");
const toggleSound = document.getElementById("setting-toggle-sound");
const toggleVibration = document.getElementById("setting-toggle-vibration");

// Estado da Aplicação
let currentStream = null;
let wakeLock = null;
let recorder = null;
let poseDetector = null;
let lastProgressBeep = 0;
let currentTunnelUrl = null;
let availableCameras = [];

// Inicialização
window.addEventListener("DOMContentLoaded", async () => {
  console.log("[App] Inicializando Replay 2.0...");
  loadSettingsIntoUI();
  setupEventListeners();

  checkInsecureContext();
  fetchTunnelInfo();

  await requestWakeLock();
  await initCamera();
  await checkBackendHealth();
});

// Mantém a tela acesa mesmo se o usuário trocar de aba e voltar
document.addEventListener("visibilitychange", async () => {
  if (wakeLock !== null && document.visibilityState === "visible") {
    await requestWakeLock();
  }
});

async function requestWakeLock() {
  if ("wakeLock" in navigator) {
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      console.log("[WakeLock] Bloqueio de tela ativado com sucesso.");
    } catch (err) {
      console.warn("[WakeLock] Falha ao ativar:", err);
    }
  }
}

function checkInsecureContext() {
  const isLocalhost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
  if (!window.isSecureContext && !isLocalhost) {
    if (insecureBanner) insecureBanner.style.display = "flex";
  }
}

async function initCamera() {
  const config = getConfig();

  // Verificação de contexto seguro (O Android bloqueia câmera em HTTP não-localhost)
  const isLocalhost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    console.error("[Camera] navigator.mediaDevices indisponível.");
    if (!isLocalhost && !window.isSecureContext) {
      if (insecureBanner) insecureBanner.style.display = "flex";
      showToast("⚠️ O Android bloqueou a câmera por ser HTTP. Acesse via HTTPS!", 8000);
      openQrModal();
    } else {
      showToast("⚠️ Câmera não suportada neste dispositivo.", 5000);
    }
    return;
  }

  let videoConstraints = {
    width: { ideal: 1280 },
    height: { ideal: 720 },
    frameRate: { ideal: 30 }
  };

  if (config.selectedCameraId) {
    videoConstraints.deviceId = { exact: config.selectedCameraId };
  } else {
    videoConstraints.facingMode = { ideal: config.cameraFacing || "environment" };
  }

  const constraints = {
    video: videoConstraints,
    audio: {
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: true
    }
  };

  try {
    if (currentStream) {
      currentStream.getTracks().forEach((t) => t.stop());
    }

    try {
      currentStream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (deviceErr) {
      // Se falhou com deviceId específico (lente removida ou alterada), tenta fallback
      if (config.selectedCameraId) {
        console.warn("[Camera] Falha com deviceId específico. Tentando câmera padrão...", deviceErr);
        saveConfig({ selectedCameraId: "" });
        constraints.video = {
          facingMode: { ideal: "environment" },
          width: { ideal: 1280 },
          height: { ideal: 720 },
          frameRate: { ideal: 30 }
        };
        currentStream = await navigator.mediaDevices.getUserMedia(constraints);
      } else {
        throw deviceErr;
      }
    }

    videoElement.srcObject = currentStream;
    await videoElement.play();

    console.log("[Camera] Feed de vídeo ativo.");

    // Atualiza a lista de câmeras disponíveis para o usuário
    await populateCameraList();

    // Inicializa o Buffer Recorder
    initRecorder(currentStream, config);

    // Inicializa o Detector de Pose
    initPoseDetector(videoElement, config);

  } catch (err) {
    console.error("[Camera] Erro ao acessar câmera:", err);
    showToast(`Erro na câmera: ${err.message}`, 5000);
  }
}

async function populateCameraList() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    availableCameras = devices.filter((d) => d.kind === "videoinput");

    if (selectCamera) {
      selectCamera.innerHTML = "";
      if (availableCameras.length === 0) {
        selectCamera.innerHTML = '<option value="">Nenhuma câmera encontrada</option>';
        return;
      }

      const config = getConfig();
      availableCameras.forEach((cam, index) => {
        const option = document.createElement("option");
        option.value = cam.deviceId;

        let label = cam.label || `Câmera ${index + 1}`;
        const lower = label.toLowerCase();
        let icon = "📷";
        if (lower.includes("front") || lower.includes("frontal") || lower.includes("user")) {
          icon = "🤳";
        } else if (lower.includes("wide") || lower.includes("ultra") || lower.includes("0.5") || lower.includes("grande")) {
          icon = "🌐";
        }

        option.textContent = `${icon} ${label}`;

        if (config.selectedCameraId && cam.deviceId === config.selectedCameraId) {
          option.selected = true;
        } else if (!config.selectedCameraId && index === 0) {
          option.selected = true;
        }

        selectCamera.appendChild(option);
      });
    }
  } catch (err) {
    console.warn("Erro ao enumerar dispositivos:", err);
  }
}

async function switchCamera(deviceId) {
  if (!deviceId) return;
  const config = getConfig();
  if (config.selectedCameraId === deviceId && currentStream) return;

  saveConfig({ selectedCameraId: deviceId });
  showToast("Alternando câmera...", 2000);
  await initCamera();
  if (selectCamera) {
    selectCamera.value = deviceId;
  }
}

async function cycleCamera() {
  if (!availableCameras || availableCameras.length === 0) {
    await populateCameraList();
  }

  if (availableCameras.length <= 1) {
    showToast("Apenas 1 câmera detectada neste aparelho.", 3000);
    return;
  }

  const config = getConfig();
  let currentIndex = availableCameras.findIndex((c) => c.deviceId === config.selectedCameraId);
  if (currentIndex === -1) {
    // Tenta identificar qual camera está atualmente ativa
    const activeTrack = currentStream ? currentStream.getVideoTracks()[0] : null;
    if (activeTrack && activeTrack.getSettings) {
      const activeSettings = activeTrack.getSettings();
      currentIndex = availableCameras.findIndex((c) => c.deviceId === activeSettings.deviceId);
    }
    if (currentIndex === -1) currentIndex = 0;
  }

  const nextIndex = (currentIndex + 1) % availableCameras.length;
  const nextCam = availableCameras[nextIndex];

  let name = nextCam.label || `Câmera ${nextIndex + 1}`;
  showToast(`Câmera: ${name}`, 2500);
  await switchCamera(nextCam.deviceId);
}

function initRecorder(stream, config) {
  if (recorder) {
    recorder.stop();
  }

  recorder = new VideoBufferRecorder({
    stream,
    bufferSeconds: parseInt(config.bufferSeconds, 10) || 30,
    chunkDuration: config.chunkDuration || 5000,
    onBufferUpdate: ({ readySeconds, targetSeconds, isReady }) => {
      bufferTimeText.textContent = `${readySeconds}s / ${targetSeconds}s`;
      if (isReady) {
        bufferStatusBadge.style.borderColor = "var(--accent-green)";
      }
    },
    onUploadStatus: ({ state, message }) => {
      if (state === "uploading") {
        showToast("Processando e enviando replay...", 3000);
        btnManualReplay.classList.add("disabled");
      } else if (state === "success") {
        showToast(message, 4000);
        btnManualReplay.classList.remove("disabled");
      } else if (state === "error") {
        showToast(message, 5000);
        btnManualReplay.classList.remove("disabled");
      }
    }
  });

  recorder.start();
}

async function initPoseDetector(video, config) {
  if (poseDetector) {
    poseDetector.stop();
  }

  poseDetector = new PoseTriggerDetector({
    videoElement: video,
    holdDurationSeconds: config.poseHoldSeconds || 3.0,
    cooldownSeconds: config.cooldownSeconds || 15,
    onStatus: ({ state, message }) => {
      const dot = aiStatusBadge.querySelector(".dot");
      if (state === "ready") {
        dot.className = "dot dot-green";
        aiStatusBadge.title = message;
      } else if (state === "error") {
        dot.className = "dot dot-red";
        aiStatusBadge.title = message;
      }
    },
    onPoseProgress: ({ progress, secondsRemaining, isHolding }) => {
      handlePoseProgress(progress, secondsRemaining, isHolding);
    },
    onTrigger: async (reason) => {
      await handleReplayTrigger(reason);
    },
    onCooldown: ({ active, secondsRemaining }) => {
      if (active) {
        cooldownPill.style.display = "block";
        cooldownSecondsText.textContent = `${secondsRemaining}s`;
        btnManualReplay.classList.add("disabled");
      } else {
        cooldownPill.style.display = "none";
        btnManualReplay.classList.remove("disabled");
      }
    }
  });

  const ok = await poseDetector.initialize();
  if (ok) {
    poseDetector.start();
  }
}

function handlePoseProgress(progress, secondsRemaining, isHolding) {
  const config = getConfig();

  if (progress > 0.05) {
    triggerContainer.classList.add("active");

    // Animação SVG do anel (perímetro = 2 * PI * 60 = ~377)
    const offset = 377 * (1 - progress);
    progressRingBar.style.strokeDashoffset = offset;

    // Muda de cor conforme chega perto de 100%
    if (progress > 0.75) {
      progressRingBar.style.stroke = "var(--accent-green)";
    } else {
      progressRingBar.style.stroke = "var(--accent-blue)";
    }

    triggerCounterText.textContent = Math.ceil(secondsRemaining);

    // Beep sutil a cada segundo
    const currentInt = Math.ceil(secondsRemaining);
    if (isHolding && currentInt !== lastProgressBeep && currentInt <= 3) {
      lastProgressBeep = currentInt;
      playCountdownBeep(config.soundEnabled);
    }

  } else {
    triggerContainer.classList.remove("active");
    progressRingBar.style.strokeDashoffset = 377;
    lastProgressBeep = 0;
  }
}

async function handleReplayTrigger(reason) {
  const config = getConfig();
  console.log(`[App] DISPARANDO REPLAY: ${reason}`);

  // Efeito de flash na tela
  flashEffect.classList.add("trigger");
  setTimeout(() => flashEffect.classList.remove("trigger"), 200);

  // Toca apito sonoro duplo e vibra o celular
  playReplayWhistle(config.soundEnabled, config.vibrationEnabled);

  // Reseta visual da mira
  triggerContainer.classList.remove("active");
  progressRingBar.style.strokeDashoffset = 377;

  // Inicia envio dos fragmentos gravados
  if (recorder) {
    await recorder.captureAndSendReplay(config.backendUrl, reason);
  }
}

async function checkBackendHealth() {
  const config = getConfig();
  const dot = backendStatusBadge.querySelector(".dot");
  try {
    const res = await fetch(`${config.backendUrl.replace(/\/$/, "")}/api/health`);
    if (res.ok) {
      const data = await res.json();
      dot.className = "dot dot-green";
      backendStatusBadge.title = `Conectado: FFmpeg ${data.ffmpeg.available ? "OK" : "Ausente"}`;
    } else {
      dot.className = "dot dot-amber";
    }
  } catch (e) {
    dot.className = "dot dot-red";
    backendStatusBadge.title = "Backend inacessível";
  }
}

function showToast(message, duration = 3000) {
  toastMessage.textContent = message;
  toastNotification.classList.add("show");
  setTimeout(() => {
    toastNotification.classList.remove("show");
  }, duration);
}

function loadSettingsIntoUI() {
  const config = getConfig();
  inputBackendUrl.value = config.backendUrl;
  selectBufferSeconds.value = String(config.bufferSeconds);
  toggleSound.checked = config.soundEnabled;
  toggleVibration.checked = config.vibrationEnabled;
  if (selectCamera && config.selectedCameraId) {
    selectCamera.value = config.selectedCameraId;
  }
  populateCameraList();
}

function setupEventListeners() {
  // Botão Trocar Câmera Rápido (Alterna entre lentes do aparelho)
  if (btnSwitchCamera) {
    btnSwitchCamera.addEventListener("click", cycleCamera);
  }

  // Mudança de Câmera no Select
  if (selectCamera) {
    selectCamera.addEventListener("change", async (e) => {
      const deviceId = e.target.value;
      if (deviceId) {
        await switchCamera(deviceId);
      }
    });
  }

  // Notifica quando dispositivo de câmera é conectado/removido
  if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
    navigator.mediaDevices.addEventListener("devicechange", populateCameraList);
  }

  // Botão Manual de Replay
  btnManualReplay.addEventListener("click", async () => {
    if (btnManualReplay.classList.contains("disabled")) return;
    if (poseDetector) {
      poseDetector.forceCooldown(15);
    }
    await handleReplayTrigger("Disparo Manual (Botão da Tela)");
  });

  // Tela Cheia
  btnFullscreen.addEventListener("click", () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  });

  // Modal de Configurações
  btnSettings.addEventListener("click", () => {
    loadSettingsIntoUI();
    modalSettings.classList.add("open");
  });

  btnCloseModal.addEventListener("click", () => {
    modalSettings.classList.remove("open");
  });

  modalSettings.addEventListener("click", (e) => {
    if (e.target === modalSettings) {
      modalSettings.classList.remove("open");
    }
  });

  btnSaveSettings.addEventListener("click", async () => {
    const config = getConfig();
    const selectedCamId = selectCamera ? selectCamera.value : "";
    const oldCamId = config.selectedCameraId;

    const newConfig = saveConfig({
      backendUrl: inputBackendUrl.value.trim() || window.location.origin,
      selectedCameraId: selectedCamId,
      bufferSeconds: parseInt(selectBufferSeconds.value, 10) || 30,
      soundEnabled: toggleSound.checked,
      vibrationEnabled: toggleVibration.checked
    });

    modalSettings.classList.remove("open");
    showToast("Configurações salvas!", 2500);

    if (selectedCamId && selectedCamId !== oldCamId) {
      await initCamera();
    } else if (currentStream) {
      // Reinicia o buffer com o novo tempo
      initRecorder(currentStream, newConfig);
    }
    await checkBackendHealth();
  });

  btnTestBackend.addEventListener("click", async () => {
    btnTestBackend.textContent = "Testando...";
    try {
      const url = inputBackendUrl.value.trim() || window.location.origin;
      const res = await fetch(`${url.replace(/\/$/, "")}/api/health`);
      if (res.ok) {
        showToast("Backend respondeu com sucesso! (200 OK)", 3000);
      } else {
        showToast(`Erro no backend: HTTP ${res.status}`, 4000);
      }
    } catch (e) {
      showToast(`Falha de conexão: ${e.message}`, 4000);
    } finally {
      btnTestBackend.textContent = "Testar Conexão";
    }
  });

  // Botão Conectar Celular (QR Code HTTPS)
  if (btnQrMobile) {
    btnQrMobile.addEventListener("click", openQrModal);
  }

  if (btnCloseQrModal) {
    btnCloseQrModal.addEventListener("click", closeQrModal);
  }

  if (modalQr) {
    modalQr.addEventListener("click", (e) => {
      if (e.target === modalQr) closeQrModal();
    });
  }

  if (btnCopyQrLink) {
    btnCopyQrLink.addEventListener("click", () => {
      const targetUrl = window.location.protocol === "https:" ? window.location.origin : currentTunnelUrl;
      if (targetUrl) {
        navigator.clipboard.writeText(targetUrl).catch(() => {});
        showToast("Link copiado para a área de transferência!", 3000);
      } else {
        showToast("Aguarde o link ser gerado...", 2500);
      }
    });
  }

  if (btnRefreshTunnel) {
    btnRefreshTunnel.addEventListener("click", async () => {
      if (window.location.protocol === "https:") {
        updateQrUI(window.location.origin);
        showToast("Conexão direta HTTPS ativa!", 2500);
        return;
      }
      btnRefreshTunnel.textContent = "🔄 Gerando...";
      showToast("Reativando túnel HTTPS Cloudflare...", 3000);
      await fetchTunnelInfo(true);
      btnRefreshTunnel.textContent = "🔄 Atualizar";
    });
  }

  if (btnGoHttps) {
    btnGoHttps.addEventListener("click", async () => {
      if (currentTunnelUrl) {
        window.location.href = currentTunnelUrl;
      } else {
        showToast("Buscando link HTTPS...", 2500);
        const url = await fetchTunnelInfo(true);
        if (url) {
          window.location.href = url;
        } else {
          openQrModal();
        }
      }
    });
  }
}

async function fetchTunnelInfo(forceStart = false) {
  // Em produção HTTPS (Easypanel), a URL já é o próprio domínio
  if (window.location.protocol === "https:") {
    currentTunnelUrl = window.location.origin;
    updateQrUI(currentTunnelUrl);
    return currentTunnelUrl;
  }

  try {
    const endpoint = forceStart ? "/api/tunnel-start" : "/api/tunnel-info";
    const method = forceStart ? "POST" : "GET";
    const res = await fetch(endpoint, { method });
    if (res.ok) {
      const data = await res.json();
      if (data.url) {
        currentTunnelUrl = data.url;
        updateQrUI(data.url);
        return data.url;
      }
    }
  } catch (err) {
    console.warn("Erro ao buscar info do túnel:", err);
  }
  return null;
}

function updateQrUI(url) {
  if (!url) return;
  if (qrLoadingText) qrLoadingText.style.display = "none";
  if (qrImage) {
    qrImage.src = `https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=${encodeURIComponent(url)}`;
    qrImage.style.display = "block";
  }
  if (qrLinkAnchor) {
    qrLinkAnchor.href = url;
    qrLinkAnchor.textContent = url;
  }
}

function openQrModal() {
  if (!modalQr) return;
  modalQr.style.display = "flex";

  // Se já estivermos em HTTPS (ex: Easypanel), usa a própria URL de produção diretamente!
  if (window.location.protocol === "https:") {
    updateQrUI(window.location.origin);
    if (btnRefreshTunnel) btnRefreshTunnel.style.display = "none";
    return;
  }

  // Ambiente de desenvolvimento local: busca o túnel Cloudflare
  if (!currentTunnelUrl) {
    pollTunnelUrl();
  } else {
    updateQrUI(currentTunnelUrl);
  }
}

function closeQrModal() {
  if (modalQr) {
    modalQr.style.display = "none";
  }
}

async function pollTunnelUrl(attempts = 10) {
  if (qrLoadingText) qrLoadingText.style.display = "block";
  for (let i = 0; i < attempts; i++) {
    const url = await fetchTunnelInfo();
    if (url) break;
    await new Promise((r) => setTimeout(r, 1500));
  }
}
