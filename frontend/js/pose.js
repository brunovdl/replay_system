/**
 * Detector de Pose via MediaPipe Tasks Vision (Lite Model)
 * Otimizado para 8-10 FPS para não aquecer o celular sob o sol.
 */

import {
  FilesetResolver,
  PoseLandmarker
} from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";

export class PoseTriggerDetector {
  constructor(options = {}) {
    this.videoElement = options.videoElement;
    this.holdDurationSeconds = options.holdDurationSeconds || 3.0;
    this.cooldownSeconds = options.cooldownSeconds || 15;
    this.onPoseProgress = options.onPoseProgress || (() => {});
    this.onTrigger = options.onTrigger || (() => {});
    this.onCooldown = options.onCooldown || (() => {});
    this.onStatus = options.onStatus || (() => {});

    this.landmarker = null;
    this.isDetecting = false;
    this.poseAccumulatedMs = 0;
    this.lastFrameTime = 0;
    this.cooldownUntil = 0;
    this.processingIntervalMs = 110; // ~9 FPS para poupar bateria e manter alta precisão
    this.loopTimer = null;
  }

  async initialize() {
    try {
      this.onStatus({ state: "loading", message: "Carregando modelo de IA..." });

      const vision = await FilesetResolver.forVisionTasks(
        "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm"
      );

      this.landmarker = await PoseLandmarker.createFromOptions(vision, {
        baseOptions: {
          modelAssetPath: "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
          delegate: "GPU"
        },
        runningMode: "VIDEO",
        numPoses: 3, // Detecta até 3 pessoas em quadra
        minPoseDetectionConfidence: 0.5,
        minPosePresenceConfidence: 0.5,
        minTrackingConfidence: 0.5
      });

      console.log("[MediaPipe] PoseLandmarker inicializado com sucesso.");
      this.onStatus({ state: "ready", message: "IA de Pose Pronta" });
      return true;

    } catch (e) {
      console.error("[MediaPipe] Erro ao inicializar modelo de pose:", e);
      this.onStatus({ state: "error", message: `Erro na IA: ${e.message}` });
      return false;
    }
  }

  start() {
    if (!this.landmarker || this.isDetecting) return;
    this.isDetecting = true;
    this.lastFrameTime = performance.now();
    this.scheduleNextDetection();
    console.log("[MediaPipe] Detecção contínua iniciada.");
  }

  stop() {
    this.isDetecting = false;
    if (this.loopTimer) {
      clearTimeout(this.loopTimer);
      this.loopTimer = null;
    }
    this.poseAccumulatedMs = 0;
    this.onPoseProgress({ progress: 0, secondsRemaining: 3.0, isHolding: false });
    console.log("[MediaPipe] Detecção contínua pausada.");
  }

  scheduleNextDetection() {
    if (!this.isDetecting) return;

    this.loopTimer = setTimeout(async () => {
      await this.detectFrame();
      this.scheduleNextDetection();
    }, this.processingIntervalMs);
  }

  async detectFrame() {
    if (!this.videoElement || this.videoElement.readyState < 2) return;

    const now = performance.now();
    const deltaMs = Math.min(now - this.lastFrameTime, 300); // Limita salto em caso de background tab
    this.lastFrameTime = now;

    // Gerenciamento de Cooldown pós-replay
    const currentTime = Date.now();
    if (currentTime < this.cooldownUntil) {
      const remainingSeconds = Math.ceil((this.cooldownUntil - currentTime) / 1000);
      this.onCooldown({ active: true, secondsRemaining: remainingSeconds });
      this.poseAccumulatedMs = 0;
      this.onPoseProgress({ progress: 0, secondsRemaining: this.holdDurationSeconds, isHolding: false });
      return;
    } else {
      this.onCooldown({ active: false, secondsRemaining: 0 });
    }

    try {
      const results = this.landmarker.detectForVideo(this.videoElement, now);
      let armRaisedDetected = false;

      if (results && results.landmarks && results.landmarks.length > 0) {
        for (const landmarks of results.landmarks) {
          // Pontos de interesse:
          // 11: ombro esquerdo, 12: ombro direito
          // 15: pulso esquerdo, 16: pulso direito
          const leftShoulder = landmarks[11];
          const rightShoulder = landmarks[12];
          const leftWrist = landmarks[15];
          const rightWrist = landmarks[16];

          // Em coordenadas de tela, Y cresce para baixo:
          // Portanto, Y do pulso menor que Y do ombro significa pulso ACIMA do ombro
          const leftArmRaised =
            leftWrist &&
            leftShoulder &&
            leftWrist.y < leftShoulder.y - 0.05 && // Margem de segurança de 5%
            (leftWrist.visibility ?? 1) > 0.4;

          const rightArmRaised =
            rightWrist &&
            rightShoulder &&
            rightWrist.y < rightShoulder.y - 0.05 &&
            (rightWrist.visibility ?? 1) > 0.4;

          if (leftArmRaised || rightArmRaised) {
            armRaisedDetected = true;
            break; // Já achou um jogador com braço erguido
          }
        }
      }

      if (armRaisedDetected) {
        this.poseAccumulatedMs += deltaMs;
        const targetMs = this.holdDurationSeconds * 1000;
        const progress = Math.min(1.0, this.poseAccumulatedMs / targetMs);
        const secondsRemaining = Math.max(0, (targetMs - this.poseAccumulatedMs) / 1000).toFixed(1);

        this.onPoseProgress({
          progress,
          secondsRemaining,
          isHolding: true
        });

        // Completou os 3 segundos!
        if (this.poseAccumulatedMs >= targetMs) {
          console.log("[MediaPipe] Gatilho de 3 segundos atingido!");
          this.poseAccumulatedMs = 0;
          this.cooldownUntil = Date.now() + (this.cooldownSeconds * 1000);
          this.onTrigger("Gatilho de Pose (Braço Erguido)");
        }
      } else {
        // Reduz o acumulador suavemente se o jogador abaixar o braço
        if (this.poseAccumulatedMs > 0) {
          this.poseAccumulatedMs = Math.max(0, this.poseAccumulatedMs - (deltaMs * 1.5));
          const targetMs = this.holdDurationSeconds * 1000;
          this.onPoseProgress({
            progress: Math.min(1.0, this.poseAccumulatedMs / targetMs),
            secondsRemaining: Math.max(0, (targetMs - this.poseAccumulatedMs) / 1000).toFixed(1),
            isHolding: false
          });
        }
      }

    } catch (err) {
      // Ignora pequenos erros de frame drops momentâneos
    }
  }

  forceCooldown(seconds) {
    this.cooldownUntil = Date.now() + (seconds * 1000);
    this.poseAccumulatedMs = 0;
  }
}
