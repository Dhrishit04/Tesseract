"""Text-to-Speech using Piper TTS (local) with OpenRouter TTS fallback."""

import os
import tempfile
from typing import Optional

import httpx

from llm.openrouter import OpenRouterClient
from skills.skill_base import SkillResult

# Optional dependencies - may not be available on all platforms
try:
    import numpy as np
    import sounddevice as sd
    VOICE_DEPS_AVAILABLE = True
except ImportError as e:
    VOICE_DEPS_AVAILABLE = False
    _import_error = e
    np = None
    sd = None


# Settings that are baked into the engine when it is created.
ENGINE_SETTINGS = ("tts_engine", "tts_voice", "tts_speed", "openrouter_tts_voice")


class PiperTTS:
    """Local TTS using Piper."""

    def __init__(
        self,
        voice: str = "en_US-lessac-medium",
        model_dir: Optional[str] = None,
        speed: float = 1.0,
    ):
        """
        Initialize Piper TTS.

        Args:
            voice: Piper voice name (e.g., "en_US-lessac-medium", "en_US-amy-low")
            model_dir: Directory containing Piper voice models
            speed: Speech speed multiplier
        """
        self.voice = voice
        self.model_dir = model_dir
        self.speed = speed
        self._piper = None
        self._sample_rate = 22050  # Piper default
        self._initialized = False

    def initialize(self) -> SkillResult:
        """Initialize Piper TTS."""
        try:
            import piper
            self._piper = piper.PiperVoice.load(
                model_path=self._get_model_path(self.voice),
                config_path=self._get_config_path(self.voice),
            )
            self._sample_rate = self._piper.config.sample_rate
            self._initialized = True
            return SkillResult.success({
                "voice": self.voice,
                "sample_rate": self._sample_rate,
            })
        except Exception as e:
            return SkillResult.failure(f"Failed to initialize Piper TTS: {e}")

    def _get_model_path(self, voice: str) -> str:
        """Get path to Piper model file."""
        if self.model_dir:
            return os.path.join(self.model_dir, f"{voice}.onnx")
        # Default to piper's model directory
        return os.path.expanduser(f"~/.local/share/piper/voices/{voice}.onnx")

    def _get_config_path(self, voice: str) -> str:
        """Get path to Piper config file."""
        if self.model_dir:
            return os.path.join(self.model_dir, f"{voice}.onnx.json")
        return os.path.expanduser(f"~/.local/share/piper/voices/{voice}.onnx.json")

    def synthesize(self, text: str) -> SkillResult:
        """Synthesize text to audio file."""
        if not self._initialized:
            init_result = self.initialize()
            if not init_result.success:
                return init_result

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                output_path = tmp.name

            # Synthesize
            self._piper.synthesize(text, output_path)

            return SkillResult.success({"audio_path": output_path})
        except Exception as e:
            return SkillResult.failure(f"Piper synthesis failed: {e}")

    def synthesize_stream(self, text: str):
        """Synthesize text and yield audio chunks for streaming playback."""
        if not self._initialized:
            init_result = self.initialize()
            if not init_result.success:
                raise RuntimeError(f"Piper not initialized: {init_result.error}")

        # Piper doesn't easily stream, so we synthesize to file then stream playback
        result = self.synthesize(text)
        if not result.success:
            raise RuntimeError(result.error)

        try:
            import wave
            with wave.open(result.data["audio_path"], "rb") as wf:
                chunk_size = 1024
                data = wf.readframes(chunk_size)
                while data:
                    yield np.frombuffer(data, dtype=np.int16)
                    data = wf.readframes(chunk_size)
        finally:
            try:
                os.unlink(result.data["audio_path"])
            except Exception:
                pass


class OpenRouterTTS:
    """Cloud TTS fallback using OpenRouter."""

    def __init__(self, client: OpenRouterClient, voice: str = "alloy", speed: float = 1.0):
        """
        Initialize OpenRouter TTS.

        Args:
            client: OpenRouter client instance
            voice: Voice to use (alloy, echo, fable, onyx, nova, shimmer)
            speed: Speech speed (0.25 to 4.0)
        """
        self.client = client
        self.voice = voice
        self.speed = speed

    async def synthesize(self, text: str) -> SkillResult:
        """Synthesize text using OpenRouter TTS."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                response = await http_client.post(
                    f"{self.client.base_url}/audio/speech",
                    headers=self.client.headers,
                    json={
                        "model": "tts-1",  # or tts-1-hd
                        "input": text,
                        "voice": self.voice,
                        "speed": self.speed,
                        "response_format": "wav",
                    },
                )
                if response.status_code != 200:
                    return SkillResult.failure(f"OpenRouter TTS error {response.status_code}: {response.text}")

                # Save to temp file
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(response.content)
                    output_path = tmp.name

                return SkillResult.success({"audio_path": output_path})
        except Exception as e:
            return SkillResult.failure(f"OpenRouter TTS failed: {e}")


class TTSEngine:
    """Unified TTS engine with Piper (local) primary and OpenRouter fallback."""

    def __init__(self, settings: dict = None):
        self.settings = settings or {}
        self._piper: Optional[PiperTTS] = None
        self._openrouter: Optional[OpenRouterTTS] = None
        self._use_piper = self.settings.get("tts_engine", "piper") == "piper"
        self._playback_thread = None
        self._playing = False

    def initialize(self) -> SkillResult:
        """Initialize TTS engines."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                f"Voice dependencies not available. Install with: pip install -e '.[voice]'. "
                f"Original error: {_import_error}"
            )

        results = {}

        # Initialize Piper (primary)
        if self._use_piper:
            self._piper = PiperTTS(
                voice=self.settings.get("tts_voice", "en_US-lessac-medium"),
                model_dir=self.settings.get("piper_model_dir"),
                speed=self.settings.get("tts_speed", 1.0),
            )
            piper_result = self._piper.initialize()
            results["piper"] = {"success": piper_result.success, "error": piper_result.error}

            if not piper_result.success:
                # Fall back to OpenRouter
                self._use_piper = False

        return SkillResult.success(results)

    def set_openrouter_client(self, client: OpenRouterClient):
        """Set OpenRouter client for fallback."""
        self._openrouter = OpenRouterTTS(
            client=client,
            voice=self.settings.get("openrouter_tts_voice", "alloy"),
            speed=self.settings.get("tts_speed", 1.0),
        )

    async def speak(self, text: str) -> SkillResult:
        """Speak text using the best available engine."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                f"Voice dependencies not available. Install with: pip install -e '.[voice]'. "
                f"Original error: {_import_error}"
            )

        if not text.strip():
            return SkillResult.failure("Empty text")

        # Try primary engine
        if self._use_piper and self._piper and self._piper._initialized:
            result = self._piper.synthesize(text)
            if result.success:
                await self._play_audio(result.data["audio_path"])
                try:
                    os.unlink(result.data["audio_path"])
                except Exception:
                    pass
                return SkillResult.success({"engine": "piper"})

        # Fall back to OpenRouter
        if self._openrouter:
            result = await self._openrouter.synthesize(text)
            if result.success:
                await self._play_audio(result.data["audio_path"])
                try:
                    os.unlink(result.data["audio_path"])
                except Exception:
                    pass
                return SkillResult.success({"engine": "openrouter"})

        return SkillResult.failure("No TTS engine available")

    async def _play_audio(self, audio_path: str):
        """Play audio file."""
        if not VOICE_DEPS_AVAILABLE:
            print("Audio playback skipped: voice dependencies not available")
            return

        self._playing = True
        try:
            import wave
            with wave.open(audio_path, "rb") as wf:
                sample_rate = wf.getframerate()
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()

                stream = sd.OutputStream(
                    samplerate=sample_rate,
                    channels=channels,
                    dtype="int16" if sampwidth == 2 else "float32",
                )
                stream.start()

                chunk_size = 1024
                data = wf.readframes(chunk_size)
                while data and self._playing:
                    if sampwidth == 2:
                        audio_data = np.frombuffer(data, dtype=np.int16)
                    else:
                        audio_data = np.frombuffer(data, dtype=np.float32)

                    if channels > 1:
                        audio_data = audio_data.reshape(-1, channels)

                    stream.write(audio_data)
                    data = wf.readframes(chunk_size)

                stream.stop()
                stream.close()
        except Exception as e:
            print(f"Audio playback error: {e}")
        finally:
            self._playing = False

    def stop(self):
        """Stop current playback."""
        self._playing = False

    def is_playing(self) -> bool:
        return self._playing


class TTSSkill:
    """Skill for text-to-speech operations."""

    def __init__(self):
        self.engine: Optional[TTSEngine] = None
        self._settings = {}
        self._rebuild_pending = False
        self._openrouter_client: Optional[OpenRouterClient] = None

    def load_settings(self, settings: dict):
        """Load voice settings; rebuild an idle engine when its own settings changed."""
        new = dict(settings.get("voice", {}))
        new.setdefault("tts_engine", "piper")
        new.setdefault("tts_voice", "en_US-lessac-medium")
        new.setdefault("tts_speed", 1.0)
        new.setdefault("openrouter_tts_voice", "alloy")
        engine = self.engine
        changed = any(new.get(k) != self._settings.get(k) for k in ENGINE_SETTINGS)
        if engine is not None and (changed or self._rebuild_pending):
            # A busy engine is rebuilt the next time settings load while it is idle.
            self._rebuild_pending = engine.is_playing()
            if not self._rebuild_pending:
                self.engine = None
        self._settings = new

    def set_openrouter_client(self, client: OpenRouterClient):
        """Set OpenRouter client for fallback; engines created later receive it too."""
        self._openrouter_client = client
        if self.engine:
            self.engine.set_openrouter_client(client)

    async def initialize(self) -> SkillResult:
        """Initialize TTS engine."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                f"Voice dependencies not available. Install with: pip install -e '.[voice]'. "
                f"Original error: {_import_error}"
            )

        if self.engine is None:
            self.engine = TTSEngine(self._settings)
            if self._openrouter_client is not None:
                self.engine.set_openrouter_client(self._openrouter_client)
        return self.engine.initialize()

    async def speak(self, text: str) -> SkillResult:
        """Speak text."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                f"Voice dependencies not available. Install with: pip install -e '.[voice]'. "
                f"Original error: {_import_error}"
            )

        if self.engine is None:
            init_result = await self.initialize()
            if not init_result.success:
                return init_result

        return await self.engine.speak(text)

    async def stop(self) -> SkillResult:
        """Stop current speech."""
        if self.engine:
            self.engine.stop()
            return SkillResult.success({"status": "stopped"})
        return SkillResult.success({"status": "not_playing"})

    def get_status(self) -> dict:
        """Get TTS status."""
        return {
            "engine": self._settings.get("tts_engine", "piper"),
            "voice": self._settings.get("tts_voice", "en_US-lessac-medium"),
            "speed": self._settings.get("tts_speed", 1.0),
            "playing": self.engine.is_playing() if self.engine else False,
            "dependencies_available": VOICE_DEPS_AVAILABLE,
        }


# Global instance
_tts_skill: Optional[TTSSkill] = None


def get_tts_skill() -> TTSSkill:
    """Get the global TTS skill instance."""
    global _tts_skill
    if _tts_skill is None:
        _tts_skill = TTSSkill()
    return _tts_skill