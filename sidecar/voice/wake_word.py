"""Wake word detection using Porcupine (Picovoice)."""

import os
import struct
import threading
import time
from typing import Callable, Optional

from skills.skill_base import SkillResult

# Optional dependencies - may not be available on all platforms
try:
    import pvporcupine
    import sounddevice as sd
    import numpy as np
    VOICE_DEPS_AVAILABLE = True
except ImportError as e:
    VOICE_DEPS_AVAILABLE = False
    _import_error = e
    pvporcupine = None
    sd = None
    np = None


# Settings that are baked into the engine when it is created.
ENGINE_SETTINGS = ("wake_word", "porcupine_access_key")


class WakeWordDetector:
    """Detects wake word using Porcupine."""

    def __init__(
        self,
        access_key: str,
        keywords: list[str] = None,
        keyword_paths: list[str] = None,
        sensitivities: list[float] = None,
        on_detected: Callable[[str], None] = None,
    ):
        """
        Initialize the wake word detector.

        Args:
            access_key: Picovoice access key (get from https://console.picovoice.ai/)
            keywords: Built-in keywords to detect (e.g., ["jarvis", "alexa", "hey google"])
            keyword_paths: Custom .ppn keyword file paths
            sensitivities: Sensitivity for each keyword (0.0 to 1.0)
            on_detected: Callback when wake word is detected (receives keyword name)
        """
        self.access_key = access_key
        self.keywords = keywords or ["jarvis"]  # Default wake word
        self.keyword_paths = keyword_paths or []
        self.sensitivities = sensitivities or [0.5] * len(self.keywords)
        self.on_detected = on_detected

        self._porcupine = None
        self._audio_stream = None
        self._running = False
        self._thread = None

    def initialize(self) -> SkillResult:
        """Initialize Porcupine."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                f"Voice dependencies not available. Install with: pip install -e '.[voice]'. "
                f"Original error: {_import_error}"
            )

        try:
            if not self.access_key:
                return SkillResult.failure(
                    "Porcupine access key not configured. Get one from https://console.picovoice.ai/"
                )

            # Combine built-in keywords and custom keyword paths
            all_keywords = list(self.keywords)
            all_sensitivities = list(self.sensitivities)

            for i, path in enumerate(self.keyword_paths):
                if not os.path.exists(path):
                    return SkillResult.failure(f"Keyword file not found: {path}")
                all_keywords.append(f"custom_{i}")
                if i < len(self.sensitivities):
                    all_sensitivities.append(self.sensitivities[i])
                else:
                    all_sensitivities.append(0.5)

            self._porcupine = pvporcupine.create(
                access_key=self.access_key,
                keywords=all_keywords if not self.keyword_paths else None,
                keyword_paths=self.keyword_paths if self.keyword_paths else None,
                sensitivities=all_sensitivities,
            )
            return SkillResult.success({"frame_length": self._porcupine.frame_length, "sample_rate": self._porcupine.sample_rate})
        except pvporcupine.PorcupineInvalidArgumentError as e:
            return SkillResult.failure(f"Invalid Porcupine arguments: {e}")
        except pvporcupine.PorcupineActivationError as e:
            return SkillResult.failure(f"Porcupine activation error: {e}")
        except Exception as e:
            return SkillResult.failure(f"Failed to initialize Porcupine: {e}")

    def start(self) -> SkillResult:
        """Start listening for wake word."""
        if self._running:
            return SkillResult.success({"status": "already_running"})

        if self._porcupine is None:
            init_result = self.initialize()
            if not init_result.success:
                return init_result

        try:
            self._running = True
            self._thread = threading.Thread(target=self._listen_loop, daemon=True)
            self._thread.start()
            return SkillResult.success({"status": "started"})
        except Exception as e:
            self._running = False
            return SkillResult.failure(f"Failed to start wake word detection: {e}")

    def stop(self) -> SkillResult:
        """Stop listening for wake word."""
        self._running = False
        if self._audio_stream:
            try:
                self._audio_stream.stop()
                self._audio_stream.close()
            except Exception:
                pass
            self._audio_stream = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        return SkillResult.success({"status": "stopped"})

    def _listen_loop(self):
        """Main audio processing loop."""
        try:
            self._audio_stream = sd.InputStream(
                samplerate=self._porcupine.sample_rate,
                blocksize=self._porcupine.frame_length,
                dtype="int16",
                channels=1,
            )
            self._audio_stream.start()

            while self._running:
                try:
                    # Read audio frame
                    audio_frame, overflowed = self._audio_stream.read(self._porcupine.frame_length)
                    if overflowed:
                        continue

                    # Process with Porcupine
                    pcm = struct.unpack_from("h" * self._porcupine.frame_length, audio_frame.tobytes())
                    keyword_index = self._porcupine.process(pcm)

                    if keyword_index >= 0:
                        # Wake word detected!
                        keyword_name = self.keywords[keyword_index] if keyword_index < len(self.keywords) else f"custom_{keyword_index - len(self.keywords)}"
                        if self.on_detected:
                            self.on_detected(keyword_name)
                        # Brief pause to avoid multiple detections
                        time.sleep(0.5)

                except Exception as e:
                    if self._running:
                        print(f"Wake word detection error: {e}")
                        time.sleep(0.1)

        except Exception as e:
            print(f"Wake word audio stream error: {e}")
        finally:
            if self._audio_stream:
                try:
                    self._audio_stream.stop()
                    self._audio_stream.close()
                except Exception:
                    pass

    def cleanup(self):
        """Clean up resources."""
        self.stop()
        if self._porcupine:
            self._porcupine.delete()
            self._porcupine = None


class WakeWordSkill:
    """Skill for controlling wake word detection."""

    def __init__(self):
        self.detector: Optional[WakeWordDetector] = None
        self._settings = {}
        self._rebuild_pending = False
        self._callback: Optional[Callable[[str], None]] = None

    def load_settings(self, settings: dict):
        """Load voice settings; rebuild an idle engine when its own settings changed."""
        new = dict(settings.get("voice", {}))
        new.setdefault("wake_word", "jarvis")
        new.setdefault("wake_word_enabled", True)
        new.setdefault("porcupine_access_key", "")
        engine = self.detector
        changed = any(new.get(k) != self._settings.get(k) for k in ENGINE_SETTINGS)
        if engine is not None and (changed or self._rebuild_pending):
            # A busy engine is rebuilt the next time settings load while it is idle.
            self._rebuild_pending = engine._running
            if not self._rebuild_pending:
                self.detector = None
        self._settings = new

    def set_detection_callback(self, callback: Callable[[str], None]) -> None:
        """Who is told about a detection, including detectors created later."""
        self._callback = callback
        if self.detector is not None:
            self.detector.on_detected = callback

    async def start_listening(self) -> SkillResult:
        """Start wake word detection."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                f"Voice dependencies not available. Install with: pip install -e '.[voice]'. "
                f"Original error: {_import_error}"
            )

        if self.detector is None:
            self.detector = WakeWordDetector(
                access_key=self._settings.get("porcupine_access_key", ""),
                keywords=[self._settings.get("wake_word", "jarvis")],
                on_detected=self._callback or self._on_wake_word,
            )

        result = self.detector.start()
        if result.success:
            self._settings["wake_word_active"] = True
        return result

    async def stop_listening(self) -> SkillResult:
        """Stop wake word detection."""
        if self.detector:
            result = self.detector.stop()
            if result.success:
                self._settings["wake_word_active"] = False
            return result
        return SkillResult.success({"status": "not_running"})

    async def _on_wake_word(self, keyword: str):
        """Callback when wake word is detected."""
        print(f"Wake word detected: {keyword}")
        # This will trigger the voice pipeline to start recording
        # The actual recording/STT will be handled by a separate skill/endpoint

    def get_status(self) -> dict:
        """Get current wake word status."""
        return {
            "enabled": self._settings.get("wake_word_enabled", False),
            "active": bool(self.detector and self.detector._running),
            "wake_word": self._settings.get("wake_word", "jarvis"),
            "has_access_key": bool(self._settings.get("porcupine_access_key")),
            "dependencies_available": VOICE_DEPS_AVAILABLE,
        }


# Global instance
_wake_word_skill: Optional[WakeWordSkill] = None


def get_wake_word_skill() -> WakeWordSkill:
    """Get the global wake word skill instance."""
    global _wake_word_skill
    if _wake_word_skill is None:
        _wake_word_skill = WakeWordSkill()
    return _wake_word_skill