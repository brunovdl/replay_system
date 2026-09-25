/**
 * Gerenciador de configurações do Replay 2.0
 */

const STORAGE_KEY = "replay2_volleyball_config";

const DEFAULT_CONFIG = {
  backendUrl: window.location.origin, // Mesma origem do FastAPI
  bufferSeconds: 30,                 // 30 segundos de buffer
  chunkDuration: 5000,               // Fatias autônomas de 5 segundos
  poseHoldSeconds: 3.0,              // 3 segundos com o braço erguido
  cooldownSeconds: 15,               // 15 segundos de cooldown
  soundEnabled: true,                // Apito sonoro ativado
  vibrationEnabled: true,            // Vibração do celular ativada
  cameraFacing: "environment",       // Câmera traseira por padrão
  selectedCameraId: "",              // ID específico do dispositivo selecionado
  preferredResolution: "720p"        // 720p ideal para quadra e upload rápido
};

export function getConfig() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      return { ...DEFAULT_CONFIG, ...JSON.parse(saved) };
    }
  } catch (e) {
    console.warn("Erro ao ler configurações do localStorage:", e);
  }
  return { ...DEFAULT_CONFIG };
}

export function saveConfig(updates) {
  try {
    const current = getConfig();
    const updated = { ...current, ...updates };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
    return updated;
  } catch (e) {
    console.error("Erro ao salvar configurações no localStorage:", e);
    return getConfig();
  }
}
