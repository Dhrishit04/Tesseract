"""Saved voice settings must reach engines that were already created."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from skills.skill_base import SkillResult
from voice import tts, wake_word

from voice.stt import STTSkill
from voice.tts import TTSSkill
from voice.wake_word import WakeWordSkill


def voice(**values):
    return {"voice": dict(values)}


class VoiceSettingsReloadTests(unittest.TestCase):
    def test_changed_stt_model_rebuilds_idle_recognizer(self):
        skill = STTSkill()
        skill.load_settings(voice(stt_model="tiny"))
        skill.stt = SimpleNamespace(_recording=False)

        skill.load_settings(voice(stt_model="small"))

        self.assertIsNone(skill.stt)

    def test_same_settings_keep_recognizer(self):
        skill = STTSkill()
        skill.load_settings(voice(stt_model="tiny"))
        engine = SimpleNamespace(_recording=False)
        skill.stt = engine

        skill.load_settings(voice(stt_model="tiny"))

        self.assertIs(skill.stt, engine)

    def test_recording_is_not_interrupted_by_a_settings_change(self):
        skill = STTSkill()
        skill.load_settings(voice(stt_model="tiny"))
        engine = SimpleNamespace(_recording=True)
        skill.stt = engine

        skill.load_settings(voice(stt_model="small"))

        self.assertIs(skill.stt, engine)

    def test_changed_voice_rebuilds_idle_speech_engine(self):
        skill = TTSSkill()
        skill.load_settings(voice(tts_voice="en_US-amy-low"))
        skill.engine = SimpleNamespace(is_playing=lambda: False)

        skill.load_settings(voice(tts_voice="en_GB-alan-low"))

        self.assertIsNone(skill.engine)

    def test_changed_wake_word_rebuilds_stopped_detector(self):
        skill = WakeWordSkill()
        skill.load_settings(voice(wake_word="jarvis"))
        skill.detector = SimpleNamespace(_running=False)

        skill.load_settings(voice(wake_word="computer"))

        self.assertIsNone(skill.detector)

    def test_wake_word_status_reflects_running_detector(self):
        skill = WakeWordSkill()
        skill.load_settings(voice(wake_word="jarvis"))
        skill.detector = SimpleNamespace(_running=True)

        self.assertTrue(skill.get_status()["active"])

    def test_loading_settings_does_not_modify_the_caller_dict(self):
        settings = voice()
        STTSkill().load_settings(settings)
        TTSSkill().load_settings(settings)
        WakeWordSkill().load_settings(settings)

        self.assertEqual(settings, {"voice": {}})


    def test_change_made_while_recording_applies_once_idle(self):
        skill = STTSkill()
        skill.load_settings(voice(stt_model="tiny"))
        engine = SimpleNamespace(_recording=True)
        skill.stt = engine

        skill.load_settings(voice(stt_model="small"))
        self.assertIs(skill.stt, engine)
        engine._recording = False
        skill.load_settings(voice(stt_model="small"))

        self.assertIsNone(skill.stt)


class VoiceWiringTests(unittest.TestCase):
    def test_new_detector_uses_the_pipeline_callback(self):
        created = []

        class FakeDetector:
            def __init__(self, access_key, keywords, on_detected):
                self.on_detected = on_detected
                self._running = False
                created.append(self)

            def start(self):
                self._running = True
                return SkillResult.success({})

        skill = WakeWordSkill()
        skill.load_settings(voice(wake_word="jarvis", porcupine_access_key="k"))
        heard = []
        skill.set_detection_callback(heard.append)
        with patch.object(wake_word, "VOICE_DEPS_AVAILABLE", True), patch.object(wake_word, "WakeWordDetector", FakeDetector):
            asyncio.run(skill.start_listening())

        created[0].on_detected("jarvis")
        self.assertEqual(heard, ["jarvis"])

    def test_tts_engine_created_later_gets_the_openrouter_client(self):
        engines = []

        class FakeEngine:
            def __init__(self, settings):
                self.client = None
                engines.append(self)

            def set_openrouter_client(self, client):
                self.client = client

            def initialize(self):
                return SkillResult.success({})

        skill = TTSSkill()
        skill.load_settings(voice())
        client = object()
        skill.set_openrouter_client(client)
        with patch.object(tts, "VOICE_DEPS_AVAILABLE", True), patch.object(tts, "TTSEngine", FakeEngine):
            asyncio.run(skill.initialize())

        self.assertIs(engines[0].client, client)


if __name__ == "__main__":
    unittest.main()
