// Voice controls. Inside the desktop app these go through Tauri commands (which also
// broadcast "voice-status-changed"); in a plain browser they call the sidecar directly.

import { invoke } from "@tauri-apps/api/core";
import { isTauri, postJson, sidecarJson } from "../lib/sidecar";

interface ComponentStatus {
  active?: boolean;
  recording?: boolean;
  playing?: boolean;
}

export interface VoiceStatus {
  wake_word: ComponentStatus;
  stt: ComponentStatus;
  tts: ComponentStatus;
  pipeline: { running?: boolean; processing?: boolean } | null;
  enabled: boolean;
  deps_available?: boolean;
}

export interface VoiceCommandResponse {
  success: boolean;
  data?: { text?: string } & Record<string, unknown>;
  error?: string;
}

function command(tauriCommand: string, path: string): Promise<VoiceCommandResponse> {
  return isTauri() ? invoke<VoiceCommandResponse>(tauriCommand) : postJson<VoiceCommandResponse>(path);
}

export function getVoiceStatus(): Promise<VoiceStatus> {
  return isTauri() ? invoke<VoiceStatus>("get_voice_status") : sidecarJson<VoiceStatus>("/voice/status");
}

export const startSttRecording = () => command("start_stt_recording", "/voice/stt/start");
export const stopSttRecording = () => command("stop_stt_recording", "/voice/stt/stop");
export const stopTts = () => command("stop_tts", "/voice/tts/stop");
export const startVoicePipeline = () => command("start_voice_pipeline", "/voice/pipeline/start");
export const stopVoicePipeline = () => command("stop_voice_pipeline", "/voice/pipeline/stop");
