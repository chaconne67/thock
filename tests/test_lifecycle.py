"""Windows lifecycle contract: no real microphone, network or user data is touched."""
import array
import asyncio
import json
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
        self.patch_enter = patch("thock.app.press_enter")
        self.press_enter = self.patch_enter.start()
        self.addCleanup(self.patch_enter.stop)
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
            watcher=SimpleNamespace(watch=Mock()), levels=deque(maxlen=18), mic_window=deque(maxlen=60), recording=None, active=set(),
            last_session_id=None, _sync_sound=Mock(), notify=Mock(), recover=Mock(), queue_report=Mock(),
            report_error=Mock(), idle_since=time.perf_counter() - 3600)
        # The live input field: records what reached the target field; ok=False means it moved.
        self.field = SimpleNamespace(writes=[], ok=True, stopped=False, failure=None, mismatch=None,
                                     target=(1, (2,), 0),
                                     restart=lambda: False)
        def update(text):
            self.field.writes.append(text)
            if not self.field.ok:
                self.field.failure, self.field.stopped = "delivery_unverified", True
            return self.field.ok
        self.field.update = update
        self.patch_field = patch("thock.app.InlineField", side_effect=lambda target, mark=None: self.field)
        self.patch_field.start()
        self.addCleanup(self.patch_field.stop)

    def start(self):
        session = self.Session(self.state, None, (1, (2,), 0))
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
        self.assertEqual(self.state.notify.call_args.kwargs, {"action": "account"})  # 확인 opens the account window

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

    async def test_no_field_when_dictation_starts_leads_back_instead_of_recording(self):
        from thock.app import WAITING
        self.field.stopped, self.field.failure = True, "range_unavailable"
        with patch("thock.app.transcribe", new_callable=AsyncMock) as speech:
            session = self.start()
            await session.task
        speech.assert_not_called()
        self.mic.close.assert_called_once()
        self.state.notify.assert_called_once_with(WAITING, action="dismiss")
        self.assertEqual(self.state.queue_report.call_args.args[2], "empty")
        self.state.recover.assert_not_called()
        self.assertEqual(self.field.mismatch, "start: unreadable")

    async def test_a_click_into_another_field_before_the_start_is_not_written_into(self):
        captured = []
        with patch("thock.app.capture_target", return_value=(1, (9,), 1)), \
                patch("thock.app.InlineField", side_effect=lambda target, mark=None: captured.append(target) or self.field):
            self.field.stopped = True
            with patch("thock.app.transcribe", new_callable=AsyncMock):
                session = self.start()
                await session.task
        self.assertEqual(captured, [None])
        self.assertEqual(self.field.mismatch, "start: another field")

    async def test_every_dictation_leaves_a_trace_without_its_text(self):
        with patch("thock.app.transcribe", new_callable=AsyncMock, return_value="비밀 문장"):
            with self.assertLogs("voicetype.trace", level="INFO") as logs:
                session = self.start()
                session.stop()
                await session.task
        line = json.loads(logs.output[0].split(":", 2)[2])
        self.assertIn("loudness", line)
        self.assertIn("replies", line)
        self.assertIn("clipped", line)
        self.assertNotIn("audio", line)  # 녹음 보관 is off by default
        names = [event[1] for event in line["events"]]
        for name in ("grant", "start_field", "stop", "transcribed", "finished"):
            self.assertIn(name, names)
        self.assertEqual(line["outcome"], "delivered")
        self.assertNotIn("비밀", logs.output[0])

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
        self.assertEqual(max(session.loudness), max(session.loudness[:1] + session.loudness))  # loud chunk kept
        self.assertGreater(max(session.loudness), 0.6)
        self.assertEqual(sum(session.replies), 1)  # one Soniox reply (heard) in this fake
        self.assertLess(record["first_audio_ms"], record["silent_start_ms"])
        self.assertLessEqual(record["silent_start_ms"], record["first_text_ms"])
        self.assertIs(session.record, record)
        self.assertLess(time.perf_counter() - self.state.idle_since, 1)

    async def test_bars_follow_this_microphones_own_noise(self):
        # A noisy microphone at rest (about -41 dBFS) and speech only 12 dB above it: the bars still go from
        # the bottom to the top. Silent blocks of a waking microphone do not count as its noise.
        noise = array.array("h", [300, -300] * 800).tobytes()
        speech = array.array("h", [1200, -1200] * 800).tobytes()
        status = SimpleNamespace(input_overflow=False)
        bars = []
        async def transcribe(chunks, api_key, context, heard, endpoint, **kwargs):
            for chunk in [bytes(3200)] * 5 + [noise] * 20 + [speech, noise]:
                session._on_audio(chunk, 1600, None, status)
                bars.append(self.state.levels[-1])
            session.stop()
            return ""
        with patch("thock.app.transcribe", side_effect=transcribe):
            session = self.start()
            await session.task
        self.assertEqual(max(bars[:25]), 0.0)
        self.assertEqual(bars[-2:], [1.0, 0.0])
        self.assertEqual(len(self.state.mic_window), 22)

    async def test_a_block_before_enter_is_recorded_and_the_text_kept(self):
        self.field.ok = False
        async def transcribe(chunks, api_key, context, heard, endpoint, **kwargs):
            heard("보낸 뒤 잘린 글")
            await asyncio.sleep(0.05)
            session.enter()  # the user pressed Enter after the notice
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
        self.press_enter.assert_not_called()  # a kept text is never sent half-written

    async def test_enter_is_pressed_after_the_last_words_are_in_and_corrected(self):
        self.state.settings["polish"] = True
        order = []
        self.field.update = lambda text: order.append(("write", text)) or True
        self.press_enter.side_effect = lambda: order.append(("enter",))
        async def transcribe(chunks, api_key, context, heard, endpoint, **kwargs):
            heard("마지막 말까지")
            await asyncio.sleep(0.05)
            session.enter()  # the user pressed Enter while the words were still coming in
            return "마지막 말까지 다 넣고"
        with patch("thock.app.transcribe", side_effect=transcribe):
            session = self.start()
            session.stop()
            await session.task
        self.assertEqual(order[-2:], [("write", "다듬은 글"), ("enter",)])  # the correction is in before Enter
        self.press_enter.reset_mock()
        with patch("thock.app.capture_target", return_value=(9, (9,), 0)):  # another window now
            async def moved(chunks, api_key, context, heard, endpoint, **kwargs):
                session.enter()
                return "다른 창"
            with patch("thock.app.transcribe", side_effect=moved):
                self.state.recording = None
                session = self.start()
                session.stop()
                await session.task
        self.press_enter.assert_not_called()

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
    def setUp(self):
        ready = patch("thock.app.ready_target", return_value=((1, (2,), 0), None, "50004/test"))
        self.ready = ready.start()
        self.addCleanup(ready.stop)

    def fake_app(self, mode):
        from thock.app import App
        from thock.config import DEFAULTS
        app = App.__new__(App)
        app.settings = {**DEFAULTS, "input_mode": mode, "welcome_complete": True}
        app.recording = None
        app.toggle = False
        app.account = SimpleNamespace(token="test", cached={"state": "signed_in", "ready": True})
        app.data_root = Path("unused")
        app.active = set()
        app.levels = deque(maxlen=18)
        app.watcher = SimpleNamespace(flush=Mock())
        app._sync_sound = Mock()
        app.last = None
        app.notice_action, app.notice_until = None, 0.0
        app.devices_changed = False
        session = Mock()
        session.settings = dict(app.settings)
        session.stop.side_effect = lambda: setattr(app, "recording", None)
        app._start_session = Mock(return_value=session)
        return app, session

    def test_no_text_field_leads_there_on_the_pill_without_recording(self):
        from thock.app import WAITING
        app, _ = self.fake_app("hold")
        self.ready.return_value = (None, "read only", "50030/")
        with self.assertLogs("voicetype.trace", level="INFO") as logs:
            app.on_key("down")
        self.assertIn('"preflight": "read only"', logs.output[0])
        app._start_session.assert_not_called()
        self.assertEqual((app.notice, app.notice_action), (WAITING, "dismiss"))  # 확인 folds the pill back
        self.ready.return_value = ((1, (2,), 0), None, "50004/test")
        app.on_key("down")
        app._start_session.assert_called_once_with((1, (2,), 0))
        self.assertIsNone(app.notice_action)  # the press answered the guide

    def test_preflight_says_why_on_the_pill_without_opening_the_microphone(self):
        app, _ = self.fake_app("hold")
        app.account.cached = {"state": "signed_in", "ready": False, "error": "이 계정의 Thock 이용권을 확인해 주세요."}
        app.on_key("down")
        app._start_session.assert_not_called()
        self.assertEqual((app.notice, app.notice_action), ("이 계정의 Thock 이용권을 확인해 주세요.", "account"))
        self.assertTrue(app.account_refresh_needed)

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


@unittest.skipUnless(sys.platform == "win32", "Windows clipboard")
class SafePaste(unittest.TestCase):
    def test_target_change_does_not_modify_clipboard_or_send_keys(self):
        from thock.win32 import paste
        with patch("thock.win32.capture_target", return_value=(20, (2,), 0)), \
             patch("thock.win32._open_clipboard") as clipboard, \
             patch("thock.win32.user32.SendInput") as send:
            self.assertIsNone(paste("test", (10, (1,), 0), None, "test"))
        clipboard.assert_not_called()
        send.assert_not_called()


@unittest.skipUnless(sys.platform == "win32", "Windows app")
class MicrophoneChoice(unittest.TestCase):
    def test_a_chosen_microphone_is_found_by_name_or_left_to_windows(self):
        from thock import app
        devices = [{"index": 0, "name": "Microsoft 사운드 매퍼 - Input", "hostapi": 0, "max_input_channels": 2},
                   {"index": 1, "name": "마이크 (USB)", "hostapi": 0, "max_input_channels": 1},
                   {"index": 2, "name": "스피커", "hostapi": 0, "max_input_channels": 0},
                   {"index": 3, "name": "마이크 (Realtek)", "hostapi": 0, "max_input_channels": 2},
                   {"index": 7, "name": "마이크 (Realtek)", "hostapi": 2, "max_input_channels": 2}]
        with patch("thock.app.sd.query_devices", return_value=devices),                 patch("thock.app.sd.default", SimpleNamespace(hostapi=0)):
            self.assertEqual(app.microphones(), ["마이크 (USB)", "마이크 (Realtek)"])  # no sound mapper, no speaker
            self.assertEqual(app.input_device("마이크 (Realtek)"), 3)  # the recording host API's number
            self.assertIsNone(app.input_device("빠진 마이크"))  # unplugged: Windows' default
            self.assertIsNone(app.input_device(None))
