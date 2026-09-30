"""Phase selection, repeat playback and bundled keyboard audio."""

import array
import http.client
import json
import sys
import tempfile
import time
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from thock import config  # noqa: E402
from thock.sound import SOUNDS, KeyboardSounds, selected_mode  # noqa: E402
from thock.settings_server import SettingsServer  # noqa: E402


class SoundSettings(unittest.TestCase):
    def test_old_settings_get_defaults_and_both_choices_persist(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "HOME", Path(folder)):
            (Path(folder) / "settings.json").write_text('{"hotkey": "scrolllock"}', encoding="utf-8")
            settings = config.load_settings()
            self.assertFalse(settings["sound_recording"])
            self.assertTrue(settings["sound_processing"])
            self.assertEqual(settings["sound_keyboard"], "rainy75")
            settings.update(sound_recording=True, sound_processing=False, sound_keyboard="ikki68")
            config.save_settings(settings)
            saved = config.load_settings()
            self.assertTrue(saved["sound_recording"])
            self.assertFalse(saved["sound_processing"])
            self.assertEqual(saved["sound_keyboard"], "ikki68")

    def test_unknown_saved_keyboard_falls_back_to_rainy75(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "HOME", Path(folder)):
            for invalid in ("unavailable", ["rainy75"]):
                (Path(folder) / "settings.json").write_text(
                    json.dumps({"sound_keyboard": invalid}), encoding="utf-8")
                self.assertEqual(config.load_settings()["sound_keyboard"], "rainy75")


class SoundPhases(unittest.TestCase):
    def test_recording_takes_precedence_over_older_processing(self):
        current, older = object(), object()
        settings = {"sound_recording": False, "sound_processing": True}
        self.assertIsNone(selected_mode(current, {current, older}, settings))
        settings["sound_recording"] = True
        self.assertEqual(selected_mode(current, {current, older}, settings), "recording")

    def test_live_typing_sound_follows_speech_while_recording(self):
        class Session:
            typing = False
        session = Session()
        settings = {"sound_recording": False, "sound_processing": True}
        self.assertIsNone(selected_mode(session, {session}, settings))
        session.typing = True
        self.assertEqual(selected_mode(session, {session}, settings), "processing")
        session.typing = False  # speech paused while the key is still down
        self.assertIsNone(selected_mode(session, {session}, settings))
        session.typing = True
        settings["sound_processing"] = False
        self.assertIsNone(selected_mode(session, {session}, settings))
        settings["sound_recording"] = True
        self.assertEqual(selected_mode(session, {session}, settings), "recording")

    def test_processing_and_silence_follow_session_completion(self):
        session = object()
        settings = {"sound_recording": True, "sound_processing": True}
        self.assertEqual(selected_mode(None, {session}, settings), "processing")
        settings["sound_processing"] = False
        self.assertIsNone(selected_mode(None, {session}, settings))
        self.assertIsNone(selected_mode(None, set(), settings))
        self.assertIsNone(selected_mode(session, set(), settings))


class SoundPlayback(unittest.TestCase):
    @staticmethod
    def wait_until(predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.005)
        if not predicate():
            raise AssertionError("sound worker did not reach the expected state")

    def test_switches_phases_and_fades_out_after_completion(self):
        class Output:
            def __init__(self):
                self.open_count = 0
                self.blocks = []

            def __enter__(self):
                self.open_count += 1
                return self

            def __exit__(self, *_):
                return False

            def write(self, data):
                samples = array.array("h", data)
                self.blocks.append((samples[0], samples[-1]))
                time.sleep(0.001)

        output = Output()
        fake = types.SimpleNamespace(RawOutputStream=lambda **_: output)
        with patch.dict(sys.modules, {"sounddevice": fake}):
            player = KeyboardSounds()
            player._audio = {mode: array.array("h", [amplitude] * 480).tobytes()
                             for mode, amplitude in ((("rainy75", "recording"), 1000),
                                                     (("ikki68", "recording"), 3000),
                                                     (("hhkb", "processing"), 2000))}
            player.set_mode("recording")
            self.wait_until(lambda: len(output.blocks) >= 3)
            player.set_mode("recording")
            player.set_mode("recording", "ikki68")
            self.wait_until(lambda: any(first == 3000 for first, _ in output.blocks[-50:]))
            player.set_mode("processing", "hhkb")
            self.wait_until(lambda: any(first == 2000 for first, _ in output.blocks[-50:]))
            before_stop = len(output.blocks)
            player.set_mode(None)
            self.wait_until(lambda: player._worker is None)
        self.assertEqual(output.open_count, 1)
        first_ikki = next(i for i, (first, _) in enumerate(output.blocks) if first == 3000)
        first_processing = next(i for i, (first, _) in enumerate(output.blocks) if first == 2000)
        self.assertEqual(output.blocks[first_ikki - 1][-1], 0)
        self.assertEqual(output.blocks[first_processing - 1][-1], 0)
        self.assertGreaterEqual(len(output.blocks) - before_stop, 20)
        tail = [abs(first) for first, _ in output.blocks[-22:]]
        self.assertTrue(all(a >= b for a, b in zip(tail, tail[1:])))
        self.assertEqual(output.blocks[-1][-1], 0)
        self.assertIsNone(player.mode)

    def test_output_failure_does_not_interrupt_dictation(self):
        class FailingOutput:
            def __enter__(self):
                raise RuntimeError("no output device")

        fake = types.SimpleNamespace(RawOutputStream=lambda **_: FailingOutput())
        with patch.dict(sys.modules, {"sounddevice": fake}), self.assertLogs("voicetype", level="WARNING"):
            player = KeyboardSounds()
            player.set_mode("processing")
            self.wait_until(lambda: player._worker is None)
        self.assertIsNone(player.mode)


class SoundAssets(unittest.TestCase):
    def test_bundled_loops_are_pcm_wav_with_quiet_boundaries(self):
        folder = Path(__file__).resolve().parent.parent / "thock" / "sounds"
        self.assertEqual(set(SOUNDS), set(config.SOUND_KEYBOARDS))
        for keyboard, phases in SOUNDS.items():
            for phase, name in phases.items():
                minimum_seconds = (45 if keyboard == "rainy75" else 25) if phase == "recording" else 15
                with self.subTest(keyboard=keyboard, phase=phase), wave.open(str(folder / name), "rb") as audio:
                    self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 48000))
                    self.assertGreater(audio.getnframes(), minimum_seconds * 48000)
                    self.assertEqual(audio.readframes(1), bytes(2))
                    audio.setpos(audio.getnframes() - 1)
                    self.assertEqual(audio.readframes(1), bytes(2))


class SoundPreview(unittest.TestCase):
    def test_only_authenticated_known_keyboards_serve_the_selected_recording(self):
        server = SettingsServer(types.SimpleNamespace())
        port = server.httpd.server_port

        def get(path, token=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request("GET", path, headers={"X-Token": token} if token else {})
                response = connection.getresponse()
                return response.status, response.getheader("Content-Type"), response.read()
            finally:
                connection.close()

        try:
            self.assertEqual(get("/api/sound-preview?keyboard=rainy75")[0], 403)
            for keyboard, phases in SOUNDS.items():
                with self.subTest(keyboard=keyboard):
                    status, content_type, body = get(
                        f"/api/sound-preview?keyboard={keyboard}", server.token)
                    expected = Path(__file__).resolve().parent.parent / "thock/sounds" / phases["processing"]
                    self.assertEqual((status, content_type), (200, "audio/wav"))
                    self.assertEqual(body, expected.read_bytes())
            for keyboard in ("missing", "../settings.html"):
                self.assertEqual(
                    get(f"/api/sound-preview?keyboard={keyboard}", server.token)[0], 404)
        finally:
            server.httpd.shutdown()
            server.httpd.server_close()


if __name__ == "__main__":
    unittest.main()
