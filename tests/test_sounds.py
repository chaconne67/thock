"""Phase selection, repeat playback and bundled keyboard audio."""

import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from thock import config  # noqa: E402
from thock.sound import KeyboardSounds, selected_mode  # noqa: E402


class SoundSettings(unittest.TestCase):
    def test_old_settings_get_defaults_and_both_choices_persist(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "HOME", Path(folder)):
            (Path(folder) / "settings.json").write_text('{"hotkey": "scrolllock"}', encoding="utf-8")
            settings = config.load_settings()
            self.assertFalse(settings["sound_recording"])
            self.assertTrue(settings["sound_processing"])
            settings.update(sound_recording=True, sound_processing=False)
            config.save_settings(settings)
            saved = config.load_settings()
            self.assertTrue(saved["sound_recording"])
            self.assertFalse(saved["sound_processing"])


class SoundPhases(unittest.TestCase):
    def test_recording_takes_precedence_over_older_processing(self):
        current, older = object(), object()
        settings = {"sound_recording": False, "sound_processing": True}
        self.assertIsNone(selected_mode(current, {current, older}, settings))
        settings["sound_recording"] = True
        self.assertEqual(selected_mode(current, {current, older}, settings), "recording")

    def test_processing_and_silence_follow_session_completion(self):
        session = object()
        settings = {"sound_recording": True, "sound_processing": True}
        self.assertEqual(selected_mode(None, {session}, settings), "processing")
        settings["sound_processing"] = False
        self.assertIsNone(selected_mode(None, {session}, settings))
        self.assertIsNone(selected_mode(None, set(), settings))
        self.assertIsNone(selected_mode(session, set(), settings))


class SoundPlayback(unittest.TestCase):
    def test_switches_loops_and_stops_without_restarting_same_mode(self):
        calls = []
        fake = types.SimpleNamespace(PlaySound=lambda name, flags: calls.append((name, flags)),
                                     SND_FILENAME=1, SND_ASYNC=2, SND_LOOP=4, SND_NODEFAULT=8)
        with patch.dict(sys.modules, {"winsound": fake}):
            player = KeyboardSounds()
            player.set_mode("recording")
            player.set_mode("recording")
            player.set_mode("processing")
            player.set_mode(None)
        self.assertEqual([Path(name).name if name else None for name, _ in calls],
                         [None, "recording.wav", None, "processing.wav", None])
        self.assertEqual(calls[1][1], 15)

    def test_playback_failure_does_not_interrupt_dictation(self):
        def fail_on_file(name, flags):
            if name:
                raise RuntimeError("no output device")
        fake = types.SimpleNamespace(PlaySound=fail_on_file,
                                     SND_FILENAME=1, SND_ASYNC=2, SND_LOOP=4, SND_NODEFAULT=8)
        with patch.dict(sys.modules, {"winsound": fake}), self.assertLogs("voicetype", level="WARNING"):
            player = KeyboardSounds()
            player.set_mode("processing")
            self.assertIsNone(player.mode)


class SoundAssets(unittest.TestCase):
    def test_bundled_loops_are_pcm_wav_with_quiet_boundaries(self):
        folder = Path(__file__).resolve().parent.parent / "thock" / "sounds"
        for name, seconds in (("recording.wav", 6.0), ("processing.wav", 2.4)):
            with self.subTest(name=name), wave.open(str(folder / name), "rb") as audio:
                self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 48000))
                self.assertEqual(audio.getnframes(), round(seconds * 48000))
                self.assertEqual(audio.readframes(48), bytes(96))
                audio.setpos(audio.getnframes() - 48)
                self.assertEqual(audio.readframes(48), bytes(96))


if __name__ == "__main__":
    unittest.main()
