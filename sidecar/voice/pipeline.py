"""Voice pipeline orchestrator — ties wake word → STT → LLM → TTS into a loop."""

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from skills.skill_base import SkillResult

logger = logging.getLogger(__name__)

# Check if voice dependencies are available
try:
    from voice.wake_word import VOICE_DEPS_AVAILABLE as WAKE_WORD_DEPS
    from voice.stt import VOICE_DEPS_AVAILABLE as STT_DEPS
    from voice.tts import VOICE_DEPS_AVAILABLE as TTS_DEPS
    VOICE_DEPS_AVAILABLE = WAKE_WORD_DEPS and STT_DEPS and TTS_DEPS
except ImportError:
    VOICE_DEPS_AVAILABLE = False


class VoicePipeline:
    """Orchestrates the full voice conversation loop.

    Flow:
        1. Wake word detected (via WakeWordDetector)
        2. Start recording audio (via WhisperSTT)
        3. Silence detected → stop recording, transcribe
        4. Send transcription to LLM for response
        5. Speak LLM response aloud (via TTSEngine)
        6. Return to wake word listening
    """

    def __init__(self):
        self._running = False
        self._processing = False
        self._on_state_change: Optional[Callable[[str], None]] = None
        self._on_transcription: Optional[Callable[[str], None]] = None
        self._on_response: Optional[Callable[[str], None]] = None
        self._llm_handler: Optional[Callable[[str], Awaitable[str]]] = None
        # Loop the server runs on; wake word callbacks arrive on the audio thread.
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # These get set by the server after initialization
        self._wake_word_skill = None
        self._stt_skill = None
        self._tts_skill = None

    def set_components(self, wake_word_skill, stt_skill, tts_skill):
        """Set the voice component skills."""
        self._wake_word_skill = wake_word_skill
        self._stt_skill = stt_skill
        self._tts_skill = tts_skill

    def set_llm_handler(self, handler: Callable):
        """Set the async function that sends text to LLM and returns response text.

        handler signature: async def handler(text: str) -> str
        """
        self._llm_handler = handler

    def set_callbacks(
        self,
        on_state_change: Callable[[str], None] = None,
        on_transcription: Callable[[str], None] = None,
        on_response: Callable[[str], None] = None,
    ):
        """Set optional callbacks for pipeline events."""
        self._on_state_change = on_state_change
        self._on_transcription = on_transcription
        self._on_response = on_response

    def _emit_state(self, state: str):
        """Emit a state change event."""
        logger.info(f"Voice pipeline state: {state}")
        if self._on_state_change:
            try:
                self._on_state_change(state)
            except Exception:
                pass

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_processing(self) -> bool:
        return self._processing

    async def start(self) -> SkillResult:
        """Start the full voice pipeline (wake word → STT → LLM → TTS loop)."""
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                "Voice dependencies not available. Install with: pip install -e '.[voice]'"
            )

        if self._running:
            return SkillResult.success({"status": "already_running"})

        if not self._wake_word_skill:
            return SkillResult.failure("Wake word skill not configured")
        if not self._stt_skill:
            return SkillResult.failure("STT skill not configured")
        if not self._tts_skill:
            return SkillResult.failure("TTS skill not configured")

        self._running = True
        self._loop = asyncio.get_running_loop()
        self._emit_state("starting")

        # Set wake word callback to trigger recording
        self._wake_word_skill.set_detection_callback(self._on_wake_word_detected)

        # Start wake word listening
        result = await self._wake_word_skill.start_listening()
        if not result.success:
            self._running = False
            return SkillResult.failure(f"Failed to start wake word: {result.error}")

        self._emit_state("listening")
        return SkillResult.success({"status": "running"})

    async def stop(self) -> SkillResult:
        """Stop the voice pipeline."""
        self._running = False
        self._emit_state("stopping")

        # Stop all components
        if self._wake_word_skill:
            await self._wake_word_skill.stop_listening()
        if self._stt_skill and self._stt_skill.stt and self._stt_skill.stt._recording:
            self._stt_skill.stt.stop_recording()
        if self._tts_skill:
            await self._tts_skill.stop()

        self._emit_state("idle")
        return SkillResult.success({"status": "stopped"})

    def _on_wake_word_detected(self, keyword: str):
        """Callback when wake word is detected — kicks off the conversation turn."""
        logger.info(f"Wake word detected: {keyword}")
        if self._processing:
            logger.info("Already processing a conversation turn, ignoring wake word")
            return

        if self._loop is None or self._loop.is_closed():
            logger.error("Wake word detected but the pipeline has no running event loop")
            return
        asyncio.run_coroutine_threadsafe(self._conversation_turn(), self._loop)

    async def _conversation_turn(self):
        """Execute a single conversation turn: record → transcribe → LLM → speak."""
        if self._processing:
            return

        self._processing = True

        try:
            # 1. Stop wake word while recording
            if self._wake_word_skill:
                await self._wake_word_skill.stop_listening()

            # 2. Start recording
            self._emit_state("recording")
            record_result = await self._stt_skill.start_recording()
            if not record_result.success:
                logger.error(f"Failed to start recording: {record_result.error}")
                self._emit_state("error")
                return

            # 3. Wait for silence detection to stop recording
            #    The WhisperSTT._record_loop handles silence detection automatically.
            #    We poll until recording stops.
            max_wait = 35  # seconds (max_duration + buffer)
            waited = 0
            while self._stt_skill.stt and self._stt_skill.stt._recording and waited < max_wait:
                await asyncio.sleep(0.2)
                waited += 0.2

            # 4. Stop recording and get transcription
            self._emit_state("transcribing")
            transcription_result = await self._stt_skill.stop_recording()

            if not transcription_result.success:
                logger.error(f"Transcription failed: {transcription_result.error}")
                self._emit_state("error")
                return

            transcribed_text = transcription_result.data.get("text", "").strip()
            if not transcribed_text:
                logger.info("Empty transcription, returning to listening")
                self._emit_state("listening")
                return

            logger.info(f"Transcribed: {transcribed_text}")
            if self._on_transcription:
                self._on_transcription(transcribed_text)

            # 5. Send to LLM
            self._emit_state("thinking")
            response_text = ""
            if self._llm_handler:
                try:
                    response_text = await self._llm_handler(transcribed_text)
                except Exception:
                    logger.exception("LLM handler error")
                    response_text = "I'm sorry, I encountered an error processing your request."
            else:
                response_text = f"I heard you say: {transcribed_text}"

            if self._on_response:
                self._on_response(response_text)

            # 6. Speak response
            if response_text:
                self._emit_state("speaking")
                speak_result = await self._tts_skill.speak(response_text)
                if not speak_result.success:
                    logger.error(f"TTS failed: {speak_result.error}")

        except Exception:
            logger.exception("Conversation turn error")
            self._emit_state("error")

        finally:
            self._processing = False

            # Resume wake word listening if pipeline is still running
            if self._running and self._wake_word_skill:
                self._emit_state("listening")
                self._wake_word_skill.set_detection_callback(self._on_wake_word_detected)
                await self._wake_word_skill.start_listening()

    async def process_text_input(self, text: str) -> SkillResult:
        """Process a text input through the LLM → TTS pipeline (skip wake word + STT).

        Useful for the frontend's manual voice trigger (press-to-talk already has text).
        """
        if not VOICE_DEPS_AVAILABLE:
            return SkillResult.failure(
                "Voice dependencies not available. Install with: pip install -e '.[voice]'"
            )

        if self._processing:
            return SkillResult.failure("Already processing a conversation turn")

        self._processing = True
        try:
            # Send to LLM
            self._emit_state("thinking")
            response_text = ""
            if self._llm_handler:
                try:
                    response_text = await self._llm_handler(text)
                except Exception:
                    logger.exception("LLM handler error")
                    response_text = "I'm sorry, I encountered an error processing your request."
            else:
                response_text = f"Echo: {text}"

            # Speak response
            if response_text:
                self._emit_state("speaking")
                await self._tts_skill.speak(response_text)

            self._emit_state("listening" if self._running else "idle")
            return SkillResult.success({"response": response_text})

        except Exception as e:
            logger.exception("Text processing error")
            return SkillResult.failure(f"Processing error: {e}")

        finally:
            self._processing = False

    def get_status(self) -> dict:
        """Get pipeline status."""
        return {
            "running": self._running,
            "processing": self._processing,
            "has_wake_word": self._wake_word_skill is not None,
            "has_stt": self._stt_skill is not None,
            "has_tts": self._tts_skill is not None,
            "has_llm_handler": self._llm_handler is not None,
            "dependencies_available": VOICE_DEPS_AVAILABLE,
        }


# Global instance
_pipeline: Optional[VoicePipeline] = None


def get_pipeline() -> VoicePipeline:
    """Get the global voice pipeline instance."""
    global _pipeline
    if _pipeline is None:
        _pipeline = VoicePipeline()
    return _pipeline
