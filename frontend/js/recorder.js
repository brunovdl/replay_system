/**
 * Gerenciador de Buffer Circular Resiliente
 * Grava em blocos autônomos de 5s para evitar corrupção de cabeçalho do MediaRecorder.
 */

export class VideoBufferRecorder {
  constructor(options = {}) {
    this.stream = options.stream;
    this.bufferSeconds = options.bufferSeconds || 30;
    this.chunkDuration = options.chunkDuration || 5000;
    this.onBufferUpdate = options.onBufferUpdate || (() => {});
    this.onUploadStatus = options.onUploadStatus || (() => {});

    this.mediaRecorder = null;
    this.bufferClips = []; // Array de { blob, mimeType, timestamp }
    this.currentChunkData = [];
    this.isRecording = false;
    this.cycleTimer = null;
    this.mimeType = this.detectSupportedMimeType();

    console.log(`[Recorder] MimeType selecionado: ${this.mimeType}`);
  }

  detectSupportedMimeType() {
    const candidateTypes = [
      "video/webm;codecs=vp8,opus",
      "video/webm;codecs=vp9,opus",
      "video/webm",
      "video/mp4;codecs=avc1,mp4a.40.2",
      "video/mp4"
    ];

    for (const type of candidateTypes) {
      if (MediaRecorder.isTypeSupported(type)) {
        return type;
      }
    }
    return ""; // Padrão do navegador
  }

  start() {
    if (this.isRecording) return;
    this.isRecording = true;
    this.bufferClips = [];
    this.startNewRecordingCycle();
    console.log("[Recorder] Gravação em buffer iniciada.");
  }

  startNewRecordingCycle() {
    if (!this.isRecording || !this.stream) return;

    this.currentChunkData = [];

    try {
      const options = this.mimeType ? { mimeType: this.mimeType } : {};
      this.mediaRecorder = new MediaRecorder(this.stream, options);

      this.mediaRecorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          this.currentChunkData.push(event.data);
        }
      };

      this.mediaRecorder.onstop = () => {
        if (this.currentChunkData.length > 0) {
          const clipBlob = new Blob(this.currentChunkData, {
            type: this.mimeType || "video/webm"
          });

          this.bufferClips.push({
            blob: clipBlob,
            timestamp: Date.now()
          });

          // Mantém no máximo a quantidade necessária para cobrir bufferSeconds
          const maxClips = Math.ceil(this.bufferSeconds / (this.chunkDuration / 1000));
          while (this.bufferClips.length > maxClips) {
            this.bufferClips.shift();
          }

          // Notifica HUD do tempo acumulado
          const currentDuration = Math.min(
            this.bufferSeconds,
            this.bufferClips.length * (this.chunkDuration / 1000)
          );
          this.onBufferUpdate({
            readySeconds: currentDuration,
            targetSeconds: this.bufferSeconds,
            isReady: currentDuration >= this.bufferSeconds
          });
        }

        // Se ainda está ativo, inicia o próximo ciclo imediatamente
        if (this.isRecording) {
          this.startNewRecordingCycle();
        }
      };

      // Inicia a gravação deste bloco
      this.mediaRecorder.start();

      // Agenda a parada deste bloco após chunkDuration ms
      this.cycleTimer = setTimeout(() => {
        if (this.mediaRecorder && this.mediaRecorder.state === "recording") {
          this.mediaRecorder.stop();
        }
      }, this.chunkDuration);

    } catch (e) {
      console.error("[Recorder] Falha ao iniciar ciclo do MediaRecorder:", e);
    }
  }

  stop() {
    this.isRecording = false;
    clearTimeout(this.cycleTimer);

    if (this.mediaRecorder && this.mediaRecorder.state === "recording") {
      this.mediaRecorder.stop();
    }
    console.log("[Recorder] Gravação em buffer parada.");
  }

  /**
   * Captura o buffer atual de ~30s e envia para o backend FastAPI
   */
  async captureAndSendReplay(backendUrl, reason = "Gatilho de Pose (Braço Erguido)") {
    if (this.bufferClips.length === 0 && this.currentChunkData.length === 0) {
      console.warn("[Recorder] Buffer vazio. Aguarde acumular vídeo.");
      return false;
    }

    // Fecha o ciclo atual antecipadamente para incluir os últimos segundos gravados
    if (this.mediaRecorder && this.mediaRecorder.state === "recording") {
      clearTimeout(this.cycleTimer);
      // O stop dispara onstop que adiciona aos clips e reinicia
      this.mediaRecorder.stop();
      // Aguarda 150ms para o evento onstop do ciclo atual persistir o último blob
      await new Promise((resolve) => setTimeout(resolve, 150));
    }

    const clipsToSend = [...this.bufferClips];
    if (clipsToSend.length === 0) return false;

    console.log(`[Recorder] Enviando ${clipsToSend.length} fragmentos de replay (${reason})...`);
    this.onUploadStatus({ state: "uploading", message: "Enviando replay..." });

    try {
      const formData = new FormData();
      formData.append("duracao", `${this.bufferSeconds}s`);
      formData.append("evento", reason);

      clipsToSend.forEach((item, index) => {
        const mime = item.blob.type || this.mimeType || "";
        const ext = mime.includes("mp4") ? "mp4" : "webm";
        formData.append("files", item.blob, `chunk_${index}.${ext}`);
      });

      const endpoint = `${backendUrl.replace(/\/$/, "")}/api/upload-replay`;
      const response = await fetch(endpoint, {
        method: "POST",
        body: formData
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${await response.text()}`);
      }

      const data = await response.json();
      console.log("[Recorder] Replay despachado com sucesso:", data);
      this.onUploadStatus({ state: "success", message: "Replay enviado para o WhatsApp!" });
      return true;

    } catch (err) {
      console.error("[Recorder] Falha ao enviar replay:", err);
      this.onUploadStatus({ state: "error", message: `Erro no envio: ${err.message}` });
      return false;
    }
  }
}
