"""Windows lifecycle contract: no real microphone, network or user data is touched."""
import array
import asyncio
import sys
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

@unittest.skipUnless(sys.platform == "win32", "Windows app")
class SessionLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from thock.config import DEFAULTS
        from thock.app import Session
        self.Session = Session
        self.mic = Mock()
        self.patch_mic = patch("thock.app.sd.RawInputStream", return_value=self.mic)
        self.patch_mic.start()
        self.addCleanup(self.patch_mic.stop)
        self.patch_target = patch("thock.app.capture_target", return_value=(1, (2,), 0))
        self.patch_target.start()
        self.addCleanup(self.patch_target.stop)
        self.patch_app = patch("thock.app.foreground_app", return_value="test-editor")
        self.patch_app.start()
        self.addCleanup(self.patch_app.stop)
        self.state = SimpleNamespace(
            settings={**DEFAULTS, "learn": False, "polish": False},
            account=SimpleNamespace(start_session=Mock(return_value={
                "session_id": "11111111-1111-1111-1111-111111111111",
                "api_key": "temporary-test", "max_session_seconds": 2})),
            notes=SimpleNamespace(terms=lambda: [], apply=lambda text: text),
            profile=SimpleNamespace(context=lambda *args: {}, history=Path("unused"), maybe_rebuild=Mock()),
            polisher=SimpleNamespace(polish=Mock(return_value="다듬은 글")),
            watcher=SimpleNamespace(watch=Mock()), levels=deque(maxlen=18), recording=None, active=set(),
            last_session_id=None, _sync_sound=Mock(), notify=Mock(), recover=Mock(), queue_report=Mock(),
            report_error=Mock(), idle_since=time.perf_counter() - 3600)
        # The live input field: records what reached the target field; ok=False means it moved.
        self.field = SimpleNamespace(writes=[], ok=True, stopped=False, failure=None, late_ms=None,
                                     restart=lambda: False)
        def update(text):
            self.field.writes.append(text)
            if not self.field.ok:
                self.field.failure, self.field.stopped = "delivery_unverified", True
            return self.field.ok
        self.field.update = update
        self.patch_field = patch("thock.app.InlineField", side_effect=lambda target: self.field)
        self.patch_field.start()
        self.addCleanup(self.patch_field.stop)

    def start(self):
        session = self.Session(self.state, None)
        self.state.recording = session
        self.state.active.add(session)
        return session

    async def test_auth_failure_stops_microphone_and_allows_next_session(self):
        from thock.account import AccountError
        self.state.account.start_session.side_effect = AccountError("signed_out")
        with patch("thock.app.transcribe", new_callable=AsyncMock) as speech:
            session = self.start()
            await session.task
        self.mic.stop.assert_called_once()
        self.mic.close.assert_called_once()
        self.assertIsNone(self.state.recording)
        self.assertFalse(self.state.active)
        self.assertTrue(session.done.done())
        speech.assert_not_called()
        self.assertEqual(self.field.writes, [])
        self.state.notify.assert_called_once()

    async def test_stop_is_idempotent_and_buffer_is_capped_to_grant(self):
        from thock.config import SAMPLE_RATE
        self.state.account.start_session.return_value["max_session_seconds"] = 1
        chunks_seen = []
        async def transcribe(chunks, *args, **kwargs):
            async for chunk in chunks:
                chunks_seen.append(chunk)
            return "테스트 문장"
        with patch("thock.app.transcribe", side_effect=transcribe):
            session = self.start()
            session._enqueue(bytes(SAMPLE_RATE * 4))
            session.stop()
            session.stop()
            await session.task
        self.assertEqual(sum(map(len, chunks_seen)), SAMPLE_RATE * 2)
        self.mic.close.assert_called_once()
        self.assertEqual(self.field.writes[-1], "테스트 문장")
        self.assertEqual(self.state.queue_report.call_args.args[1:3], (1000, "delivered"))

    async def test_polish_failure_retains_raw_text_without_opening_settings(self):
        from thock.account import AccountError
        self.state.settings["polish"] = True
        self.state.polisher.polish.side_effect = AccountError("provider_unavailable")
        with patch("thock.app.transcribe", new_callable=AsyncMock, return_value="인식한 원문"):
            session = self.start()
            session.stop()
            await session.task
        self.assertEqual(self.field.writes[-1], "인식한 원문")
        self.assertEqual(self.state.queue_report.call_args.args[2], "recovered")
        self.state.notify.assert_called_once()

    async def test_changed_target_keeps_text_for_explicit_recovery(self):
        self.field.ok = False
        with patch("thock.app.transcribe", new_callable=AsyncMock, return_value="보관할 글"):
            session = self.start()
            session.stop()
            await session.task
        self.state.recover.assert_called_once()
        self.assertEqual(self.state.recover.call_args.args[0], "보관할 글")
        self.assertFalse(self.state.active)

    async def test_first_words_timing_is_recorded(self):
        quiet, loud = bytes(1600), array.array("h", [12000, -12000] * 800).tobytes()
        status = SimpleNamespace(input_overflow=False)
        async def transcribe(chunks, api_key, context, heard, endpoint, **kwargs):
            session._on_audio(quiet, 1600, None, status)
            await asyncio.sleep(0.02)
            session._on_audio(loud, 1600, None, status)
            await asyncio.sleep(0.02)
            heard("첫 단어")
            session.stop()  # audio is only measured while recording
            return "첫 단어"
        with patch("thock.app.transcribe", side_effect=transcribe):
            session = self.start()
            await session.task
        record = self.state.report_error.call_args.args[1]
        self.assertGreaterEqual(record["idle_s"], 3599)
        self.assertLess(record["first_audio_ms"], record["silent_start_ms"])
        self.assertLessEqual(record["silent_start_ms"], record["first_text_ms"])
        self.assertIs(session.record, record)
        self.assertLess(time.perf_counter() - self.state.idle_since, 1)

    async def test_a_block_before_enter_is_recorded_and_the_text_kept(self):
        self.field.ok = False
        async def transcribe(chunks, api_key, context, heard, endpoint, **kwargs):
            heard("보낸 뒤 잘린 글")
            await asyncio.sleep(0.05)
            session.enter()  # the user pressed Enter after the red notice
            return "보낸 뒤 잘린 글"
        with patch("thock.app.transcribe", side_effect=transcribe):
            session = self.start()
            session.stop()
            await session.task
        self.state.recover.assert_called_once()
        self.assertEqual(self.state.recover.call_args.args[0], "보낸 뒤 잘린 글")
        self.assertEqual(self.state.queue_report.call_args.args[2], "recovered")
        record = self.state.report_error.call_args.args[1]
        self.assertEqual((record["input_failure"], record["ended"]), ("delivery_unverified", "enter"))

    async def test_stream_failure_stops_mic_and_does_not_paste_partial_text(self):
        with patch("thock.app.transcribe", new_callable=AsyncMock, side_effect=TimeoutError):
            session = self.start()
            session.preview = "아직 확정되지 않은 글"
            await session.task
        self.mic.close.assert_called_once()
        self.assertEqual(self.field.writes, [])
        self.state.recover.assert_called_once()
        self.assertFalse(self.state.active)


@unittest.skipUnless(sys.platform == "win32", "Windows hotkey integration")
class InputModes(unittest.TestCase):
    def fake_app(self, mode):
        from thock.app import App
        from thock.config import DEFAULTS
        app = App.__new__(App)
        app.settings = {**DEFAULTS, "input_mode": mode, "welcome_complete": True}
        app.recording = None
        app.toggle = False
        app.pressed_at = 0
        app.account = SimpleNamespace(token="test")
        app.data_root = Path("unused")
        app.active = set()
        app.levels = deque(maxlen=18)
        app.watcher = SimpleNamespace(flush=Mock())
        app._sync_sound = Mock()
        app.last = None
        session = Mock()
        session.settings = dict(app.settings)
        session.stop.side_effect = lambda: setattr(app, "recording", None)
        app._start_session = Mock(return_value=session)
        return app, session

    def test_hold_releases_even_after_short_press(self):
        app, session = self.fake_app("hold")
        app.on_key("down")
        app.on_key("up")
        session.stop.assert_called_once()

    def test_toggle_stays_on_until_second_press(self):
        app, session = self.fake_app("toggle")
        app.on_key("down")
        app.on_key("up")
        session.stop.assert_not_called()
        app.on_key("down")
        session.stop.assert_called_once()

    def test_legacy_short_press_still_toggles(self):
        app, session = self.fake_app("auto")
        app.on_key("down")
        app.on_key("up")
        self.assertTrue(app.toggle)
        session.stop.assert_not_called()
        app.on_key("down")
        session.stop.assert_called_once()


@unittest.skipUnless(sys.platform == "win32", "Windows clipboard")
class SafePaste(unittest.TestCase):
    def test_target_change_does_not_modify_clipboard_or_send_keys(self):
        from thock.win32 import paste
        with patch("thock.win32.capture_target", return_value=(20, (2,), 0)), \
             patch("thock.win32._open_clipboard") as clipboard, \
             patch("thock.win32.user32.SendInput") as send:
            self.assertIsNone(paste("test", (10, (1,), 0), None, ("test", "", "")))
        clipboard.assert_not_called()
        send.assert_not_called()
