import { useCallback, useEffect, useState } from "react";
import { isTauri, errorMessage } from "../lib/sidecar";
import {
  VoiceCommandResponse,
  VoiceStatus,
  getVoiceStatus,
  startSttRecording,
  startVoicePipeline,
  stopSttRecording,
  stopTts,
  stopVoicePipeline,
} from "../services/voice";

interface VoiceControlsProps {
  onTranscription: (text: string) => void;
  onOpenSettings: () => void;
}

type VoicePhase = "unavailable" | "idle" | "listening" | "recording" | "speaking";

function phaseOf(status: VoiceStatus | null): VoicePhase {
  if (!status?.enabled || status.deps_available === false) return "unavailable";
  if (status.tts.playing) return "speaking";
  if (status.stt.recording) return "recording";
  if (status.pipeline?.running || status.wake_word.active) return "listening";
  return "idle";
}

export function VoiceControls({ onTranscription, onOpenSettings }: Readonly<VoiceControlsProps>) {
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getVoiceStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void refresh();
    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    import("@tauri-apps/api/event")
      .then(({ listen }) => listen("voice-status-changed", () => void refresh()))
      .then((stop) => {
        unlisten = stop;
      })
      .catch((error) => console.error("Could not listen for voice events", error));
    return () => unlisten?.();
  }, [refresh]);

  const run = async (action: () => Promise<VoiceCommandResponse>) => {
    setBusy(true);
    setError("");
    try {
      const result = await action();
      if (!result.success) setError(result.error ?? "Voice command failed.");
      return result;
    } catch (failure) {
      setError(errorMessage(failure));
      return null;
    } finally {
      setBusy(false);
      void refresh();
    }
  };

  const phase = phaseOf(status);

  if (phase === "unavailable") {
    const hint = status?.deps_available === false
      ? "Voice packages are not installed on this machine."
      : "Turn on voice in Settings to talk to Tesseract.";
    return (
      <button type="button" className="quiet-button" onClick={onOpenSettings} title={hint}>
        Set up voice
      </button>
    );
  }

  const stopRecording = async () => {
    const result = await run(stopSttRecording);
    const text = result?.data?.text?.trim();
    if (text) onTranscription(text);
  };

  return (
    <div className="voice-controls">
      {phase === "idle" && (
        <>
          <button type="button" className="quiet-button" disabled={busy} onClick={() => void run(startVoicePipeline)}>
            Listen for wake word
          </button>
          <button type="button" className="quiet-button" disabled={busy} onClick={() => void run(startSttRecording)}>
            Dictate
          </button>
        </>
      )}
      {phase === "listening" && (
        <>
          <span className="voice-state" role="status">Listening</span>
          <button type="button" className="quiet-button" disabled={busy} onClick={() => void run(stopVoicePipeline)}>
            Stop listening
          </button>
        </>
      )}
      {phase === "recording" && (
        <>
          <span className="voice-state voice-state-live" role="status">Recording</span>
          <button type="button" className="quiet-button" disabled={busy} onClick={() => void stopRecording()}>
            Stop and send
          </button>
        </>
      )}
      {phase === "speaking" && (
        <button type="button" className="quiet-button" disabled={busy} onClick={() => void run(stopTts)}>
          Stop speaking
        </button>
      )}
      {error && <span className="field-error" role="alert">{error}</span>}
    </div>
  );
}
