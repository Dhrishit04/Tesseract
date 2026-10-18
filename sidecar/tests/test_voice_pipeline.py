"""Voice pipeline orchestration with the audio components replaced by fakes."""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from skills.skill_base import SkillResult
from voice import pipeline as pipeline_module
from voice.pipeline import VoicePipeline


class FakeWakeWord:
    def __init__(self, log):
        self.log = log
        self.detector = SimpleNamespace(on_detected=None)

    def set_detection_callback(self, callback):
        self.detector.on_detected = callback

    async def start_listening(self):
        self.log.append("listen")
        return SkillResult.success({})

    async def stop_listening(self):
        self.log.append("stop_listen")
        return SkillResult.success({})


class FakeSTT:
    def __init__(self, log, text):
        self.log = log
        self.text = text
        self.stt = SimpleNamespace(_recording=False)

    async def start_recording(self):
        self.log.append("record")
        return SkillResult.success({})

    async def stop_recording(self):
        self.log.append("transcribe")
        return SkillResult.success({"text": self.text})


class FakeTTS:
    def __init__(self, log):
        self.log = log
        self.spoken = []

    async def speak(self, text):
        self.log.append("speak")
        self.spoken.append(text)
        return SkillResult.success({})

    async def stop(self):
        return SkillResult.success({})


def build_pipeline(text="what is on my calendar", handler=None):
    log: list[str] = []
    pipeline = VoicePipeline()
    wake, stt, tts = FakeWakeWord(log), FakeSTT(log, text), FakeTTS(log)
    pipeline.set_components(wake, stt, tts)
    loops = []

    async def default_handler(prompt):
        loops.append(asyncio.get_running_loop())
        log.append("llm")
        return f"answer to {prompt}"

    pipeline.set_llm_handler(handler or default_handler)
    return pipeline, log, wake, tts, loops


@patch.object(pipeline_module, "VOICE_DEPS_AVAILABLE", True)
class VoicePipelineTests(unittest.TestCase):
    def test_wake_word_from_audio_thread_runs_turn_on_server_loop(self):
        async def scenario():
            pipeline, log, wake, tts, loops = build_pipeline()
            await pipeline.start()
            # The detector invokes its callback from its own capture thread.
            thread = threading.Thread(target=wake.detector.on_detected, args=("jarvis",))
            thread.start()
            thread.join(timeout=5)
            for _ in range(50):
                if "speak" in log and log[-1] == "listen":
                    break
                await asyncio.sleep(0.02)
            return log, tts, loops, asyncio.get_running_loop()

        log, tts, loops, server_loop = asyncio.run(scenario())

        self.assertEqual(log, ["listen", "stop_listen", "record", "transcribe", "llm", "speak", "listen"])
        self.assertEqual(tts.spoken, ["answer to what is on my calendar"])
        self.assertEqual(loops, [server_loop])

    def test_empty_transcription_skips_llm_and_resumes_listening(self):
        async def scenario():
            pipeline, log, _, tts, _ = build_pipeline(text="   ")
            await pipeline.start()
            await pipeline._conversation_turn()
            return log, tts

        log, tts = asyncio.run(scenario())

        self.assertNotIn("llm", log)
        self.assertEqual(tts.spoken, [])
        self.assertEqual(log[-1], "listen")

    def test_llm_failure_is_spoken_as_apology(self):
        async def failing(_prompt):
            raise RuntimeError("upstream down")

        async def scenario():
            pipeline, _, _, tts, _ = build_pipeline(handler=failing)
            result = await pipeline.process_text_input("hello")
            return result, tts

        result, tts = asyncio.run(scenario())

        self.assertTrue(result.success)
        self.assertEqual(len(tts.spoken), 1)
        self.assertIn("sorry", tts.spoken[0].lower())

    def test_start_reports_missing_dependencies(self):
        with patch.object(pipeline_module, "VOICE_DEPS_AVAILABLE", False):
            result = asyncio.run(VoicePipeline().start())

        self.assertFalse(result.success)
        self.assertIn("pip install", result.error)


if __name__ == "__main__":
    unittest.main()
