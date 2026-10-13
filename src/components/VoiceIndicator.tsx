import { useState, useEffect } from "react";
import { listen } from "@tauri-apps/api/event";
import {
  getVoiceStatus,
  startSttRecording,
  stopSttRecording,
  stopTts,
  startVoicePipeline,
  stopVoicePipeline,
} from "../services/voice";

export type VoiceState = "idle" | "listening" | "recording" | "speaking" | "processing";

interface VoiceIndicatorProps {
  onTranscription?: (text: string) => void;
}

export function VoiceIndicator({ onTranscription }: VoiceIndicatorProps) {
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [wakeWordActive, setWakeWordActive] = useState(false);
  const [sttRecording, setSttRecording] = useState(false);
  const [ttsPlaying, setTtsPlaying] = useState(false);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [pipelineProcessing, setPipelineProcessing] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(false);

  // Load initial voice status
  const loadVoiceStatus = async () => {
    if (!("__TAURI_INTERNALS__" in window)) return;
    try {
      const status = await getVoiceStatus();
      setVoiceEnabled(status.enabled);
      setWakeWordActive(status.wake_word.active);
      setSttRecording(status.stt.recording);
      setTtsPlaying(status.tts.playing);
      setPipelineRunning(status.pipeline?.running || false);
      setPipelineProcessing(status.pipeline?.processing || false);
      updateVoiceState();
    } catch (err) {
      console.error("Failed to load voice status:", err);
    }
  };

  const updateVoiceState = () => {
    if (pipelineProcessing || ttsPlaying) {
      setVoiceState("processing");
      if (ttsPlaying) setVoiceState("speaking");
    } else if (sttRecording) {
      setVoiceState("recording");
    } else if (pipelineRunning || wakeWordActive) {
      setVoiceState("listening");
    } else {
      setVoiceState("idle");
    }
  };

  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return;
    loadVoiceStatus();

    // Listen for voice status changes from sidecar
    let unlisten: () => void;
    listen("voice-status-changed", () => {
      loadVoiceStatus();
    }).then((fn) => {
      unlisten = fn;
    });

    return () => {
      if (unlisten) unlisten();
    };
  }, []);

  const handlePipelineToggle = async () => {
    try {
      if (pipelineRunning) {
        await stopVoicePipeline();
        setPipelineRunning(false);
      } else {
        await startVoicePipeline();
        setPipelineRunning(true);
      }
      updateVoiceState();
    } catch (err) {
      console.error("Failed to toggle voice pipeline:", err);
    }
  };

  const handleSttToggle = async () => {
    try {
      if (sttRecording) {
        const result = await stopSttRecording();
        if (result.success && result.data?.text && onTranscription) {
          onTranscription(result.data.text);
        }
        setSttRecording(false);
      } else {
        await startSttRecording();
        setSttRecording(true);
      }
      updateVoiceState();
    } catch (err) {
      console.error("Failed to toggle STT recording:", err);
    }
  };

  const handleStopTts = async () => {
    try {
      await stopTts();
      setTtsPlaying(false);
      updateVoiceState();
    } catch (err) {
      console.error("Failed to stop TTS:", err);
    }
  };

  const getStateConfig = () => {
    switch (voiceState) {
      case "listening":
        return {
          icon: "voice",
          color: "text-tesseract-accent",
          bgColor: "bg-tesseract-accent/10",
          pulse: true,
          tooltip: "Listening for wake word...",
        };
      case "recording":
        return {
          icon: "recording",
          color: "text-red-400",
          bgColor: "bg-red-400/10",
          pulse: true,
          tooltip: "Recording...",
        };
      case "speaking":
        return {
          icon: "speaking",
          color: "text-green-400",
          bgColor: "bg-green-400/10",
          pulse: true,
          tooltip: "Speaking...",
        };
      case "processing":
        return {
          icon: "working",
          color: "text-yellow-400",
          bgColor: "bg-yellow-400/10",
          pulse: true,
          tooltip: "Processing...",
        };
      default:
        return {
          icon: "voice",
          color: voiceEnabled ? "text-tesseract-text-muted" : "text-tesseract-text-muted/50",
          bgColor: "bg-tesseract-surface",
          pulse: false,
          tooltip: voiceEnabled ? "Voice ready (click to listen)" : "Voice disabled",
        };
    }
  };

  const config = getStateConfig();

  if (!voiceEnabled) {
    return (
      <button
        onClick={() => {}}
        disabled
        className={`p-2 rounded-lg transition-all ${config.bgColor} ${config.color} hover:bg-tesseract-border`}
        title="Enable voice in Settings to use this feature"
      >
        <span className="text-[10px] uppercase tracking-wide">{config.icon}</span>
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2">
      {/* Main voice button */}
      <button
        onClick={voiceState === "recording" ? handleSttToggle : handlePipelineToggle}
        disabled={voiceState === "speaking" || voiceState === "processing"}
        className={`
          p-2 rounded-lg transition-all
          ${config.bgColor} ${config.color}
          hover:bg-tesseract-border
          disabled:opacity-50 disabled:cursor-not-allowed
          ${config.pulse ? "animate-pulse" : ""}
        `}
        title={config.tooltip}
      >
        <span className="text-[10px] uppercase tracking-wide">{config.icon}</span>
      </button>

      {/* STT recording button (shown when wake word active or manually triggered) */}
      {voiceState === "listening" && (
        <button
          onClick={handleSttToggle}
          className="p-2 rounded-lg bg-tesseract-surface border border-tesseract-border text-tesseract-text-muted hover:bg-tesseract-border hover:text-tesseract-text transition-colors"
          title="Start recording manually"
        >
          <span className="text-[10px] uppercase tracking-wide">record</span>
        </button>
      )}

      {/* Stop TTS button */}
      {voiceState === "speaking" && (
        <button
          onClick={handleStopTts}
          className="p-2 rounded-lg bg-tesseract-surface border border-tesseract-border text-tesseract-text-muted hover:bg-tesseract-border hover:text-tesseract-text transition-colors"
          title="Stop speaking"
        >
          <span className="text-[10px] uppercase tracking-wide">stop</span>
        </button>
      )}

      {/* Status indicator dot */}
      <div
        className={`
          w-2 h-2 rounded-full transition-colors
          ${voiceState === "idle"
            ? "bg-tesseract-text-muted/30"
            : voiceState === "listening"
            ? "bg-tesseract-accent animate-pulse"
            : voiceState === "recording"
            ? "bg-red-400 animate-pulse"
            : voiceState === "speaking"
            ? "bg-green-400 animate-pulse"
            : "bg-yellow-400 animate-pulse"
          }
        `}
      />
    </div>
  );
}

export default VoiceIndicator;