"""Speech-to-Text using Whisper.cpp."""

import os
import tempfile
import threading
from typing import Callable, Optional

from skills.skill_base import SkillResult

# Optional dependencies - may not be available on all platforms
try:
    import numpy as np
    import sounddevice as sd
    import whisper_cpp_python
    VOICE_DEPS_AVAILABLE = True
except ImportError as e:
    VOICE_DEPS_AVAILABLE = False
    _import_error = e
    np = None
    sd = None
    whisper_cpp_python = None


# Settings that are baked into the engine when it is created.
ENGINE_SETTINGS = ("stt_model", "stt_language")


class WhisperSTT:
    """Speech-to-Text using Whisper.cpp."""

    def __init__(
        self,
        model_name: str = "tiny",
        model_path: Optional[str] = None,
        language: str = "en",
        on_transcription: Callable[[str], None] = None,
    ):
        """
        Initialize Whisper STT.

        Args:
            model_name: Whisper model size (tiny, base, small, medium, large)
            model_path: Path to custom .ggml model file
            language: Language code (e.g., 'en', 'es', 'fr')
            on_transcription: Callback when transcription is complete
        """
        self.model_name = model_name
        self.model_path = model_path
        self.language = language
        self.on_transcription = on_transcription

        self._model = None
        self._sample_rate = 16000  # Whisper expects 16kHz
        self._recording = False
        self._audio_buffer = []
        self._record_thread = None
        self._silence_threshold = 0.01
        self._silence_duration = 1.5  # seconds of silence to stop recording
        self._max_duration = 30  # max recording duration in seconds

    def initialize(self) -> SkillResult:
        """Load the Whisper model."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                f"Voice dependencies not available. Install with: pip install -e '.[voice]'. "
                f"Original error: {_import_error}"
            )

        try:
            if self.model_path and os.path.exists(self.model_path):
                self._model = whisper_cpp_python.Whisper.from_pretrained(self.model_path)
            else:
                # Download and load the model
                self._model = whisper_cpp_python.Whisper.from_pretrained(f"ggml-{self.model_name}.bin")

            return SkillResult.success({
                "model": self.model_name,
                "sample_rate": self._sample_rate,
            })
        except Exception as e:
            return SkillResult.failure(f"Failed to load Whisper model: {e}")

    def transcribe_file(self, audio_path: str) -> SkillResult:
        """Transcribe an audio file."""
        if self._model is None:
            init_result = self.initialize()
            if not init_result.success:
                return init_result

        try:
            result = self._model.transcribe(audio_path, language=self.language)
            text = result.get("text", "").strip()
            return SkillResult.success({"text": text})
        except Exception as e:
            return SkillResult.failure(f"Transcription failed: {e}")

    def transcribe_audio(self, audio_data: np.ndarray) -> SkillResult:
        """Transcribe raw audio data (16kHz, mono, int16 or float32)."""
        if self._model is None:
            init_result = self.initialize()
            if not init_result.success:
                return init_result

        try:
            # Ensure audio is float32 in range [-1, 1]
            if audio_data.dtype == np.int16:
                audio_data = audio_data.astype(np.float32) / 32768.0
            elif audio_data.dtype != np.float32:
                audio_data = audio_data.astype(np.float32)

            # Save to temp file for whisper.cpp
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                import wave
                with wave.open(tmp.name, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)  # 16-bit
                    wf.setframerate(self._sample_rate)
                    # Convert back to int16 for wave
                    int_data = (audio_data * 32767).astype(np.int16)
                    wf.writeframes(int_data.tobytes())
                temp_path = tmp.name

            try:
                result = self._model.transcribe(temp_path, language=self.language)
                text = result.get("text", "").strip()
                return SkillResult.success({"text": text})
            finally:
                os.unlink(temp_path)

        except Exception as e:
            return SkillResult.failure(f"Transcription failed: {e}")

    def start_recording(self) -> SkillResult:
        """Start recording audio for transcription."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                f"Voice dependencies not available. Install with: pip install -e '.[voice]'. "
                f"Original error: {_import_error}"
            )

        if self._recording:
            return SkillResult.success({"status": "already_recording"})

        if self._model is None:
            init_result = self.initialize()
            if not init_result.success:
                return init_result

        try:
            self._recording = True
            self._audio_buffer = []
            self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
            self._record_thread.start()
            return SkillResult.success({"status": "recording"})
        except Exception as e:
            self._recording = False
            return SkillResult.failure(f"Failed to start recording: {e}")

    def stop_recording(self) -> SkillResult:
        """Stop recording and transcribe."""
        if not self._recording:
            return SkillResult.success({"status": "not_recording"})

        self._recording = False
        if self._record_thread:
            self._record_thread.join(timeout=2.0)
            self._record_thread = None

        if not self._audio_buffer:
            return SkillResult.failure("No audio recorded")

        # Concatenate audio chunks
        audio_data = np.concatenate(self._audio_buffer)
        self._audio_buffer = []

        # Transcribe
        return self.transcribe_audio(audio_data)

    def _record_loop(self):
        """Record audio with silence detection."""
        try:
            stream = sd.InputStream(
                samplerate=self._sample_rate,
                blocksize=1024,
                dtype="float32",
                channels=1,
            )
            stream.start()

            silence_frames = 0
            frames_per_second = self._sample_rate / 1024
            max_silence_frames = int(self._silence_duration * frames_per_second)
            max_frames = int(self._max_duration * frames_per_second)
            frame_count = 0

            while self._recording and frame_count < max_frames:
                audio_chunk, overflowed = stream.read(1024)
                if overflowed:
                    continue

                self._audio_buffer.append(audio_chunk.copy())
                frame_count += 1

                # Check for silence
                rms = np.sqrt(np.mean(audio_chunk**2))
                if rms < self._silence_threshold:
                    silence_frames += 1
                    if silence_frames >= max_silence_frames:
                        # Enough silence - stop recording
                        break
                else:
                    silence_frames = 0

            stream.stop()
            stream.close()

        except Exception as e:
            print(f"Recording error: {e}")


class STTSkill:
    """Skill for speech-to-text operations."""

    def __init__(self):
        self.stt: Optional[WhisperSTT] = None
        self._settings = {}
        self._rebuild_pending = False

    def load_settings(self, settings: dict):
        """Load voice settings; rebuild an idle engine when its own settings changed."""
        new = dict(settings.get("voice", {}))
        new.setdefault("stt_model", "tiny")
        new.setdefault("stt_language", "en")
        engine = self.stt
        changed = any(new.get(k) != self._settings.get(k) for k in ENGINE_SETTINGS)
        if engine is not None and (changed or self._rebuild_pending):
            # A busy engine is rebuilt the next time settings load while it is idle.
            self._rebuild_pending = engine._recording
            if not self._rebuild_pending:
                self.stt = None
        self._settings = new

    async def transcribe(self, audio_path: str = None) -> SkillResult:
        """Transcribe audio file or start/stop recording."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                f"Voice dependencies not available. Install with: pip install -e '.[voice]'. "
                f"Original error: {_import_error}"
            )

        if self.stt is None:
            self.stt = WhisperSTT(
                model_name=self._settings.get("stt_model", "tiny"),
                language=self._settings.get("stt_language", "en"),
            )

        if audio_path:
            # Transcribe file
            return self.stt.transcribe_file(audio_path)
        else:
            # Start/stop recording based on current state
            if self.stt._recording:
                return self.stt.stop_recording()
            else:
                return self.stt.start_recording()

    async def start_recording(self) -> SkillResult:
        """Start recording for STT."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                f"Voice dependencies not available. Install with: pip install -e '.[voice]'. "
                f"Original error: {_import_error}"
            )

        if self.stt is None:
            self.stt = WhisperSTT(
                model_name=self._settings.get("stt_model", "tiny"),
                language=self._settings.get("stt_language", "en"),
            )
        return self.stt.start_recording()

    async def stop_recording(self) -> SkillResult:
        """Stop recording and transcribe."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                f"Voice dependencies not available. Install with: pip install -e '.[voice]'. "
                f"Original error: {_import_error}"
            )

        if self.stt is None:
            return SkillResult.failure("STT not initialized")
        return self.stt.stop_recording()

    def get_status(self) -> dict:
        """Get STT status."""
        return {
            "model": self._settings.get("stt_model", "tiny"),
            "language": self._settings.get("stt_language", "en"),
            "recording": self.stt._recording if self.stt else False,
            "initialized": self.stt is not None and self.stt._model is not None,
            "dependencies_available": VOICE_DEPS_AVAILABLE,
        }


# Global instance
_stt_skill: Optional[STTSkill] = None


def get_stt_skill() -> STTSkill:
    """Get the global STT skill instance."""
    global _stt_skill
    if _stt_skill is None:
        _stt_skill = STTSkill()
    return _stt_skill