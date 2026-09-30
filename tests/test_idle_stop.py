"""Dictation also ends without a second hotkey press: after silence, or when Enter sends it."""
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


@unittest.skipUnless(sys.platform == "win32", "the app imports Windows audio and input")
class IdleStop(unittest.TestCase):
    def test_only_a_silent_tap_started_dictation_stops(self):
        from thock import app
        stopped, scheduled = [], []
        state = types.SimpleNamespace(toggle=True, _stop=lambda: stopped.append(True))
        session = types.SimpleNamespace(state=state, started=100.0, heard_at=None, _stop_when_idle=None,
                                        loop=types.SimpleNamespace(call_later=lambda delay, _: scheduled.append(delay)))
        state.recording = session
        def check(now):
            with patch("thock.app.time.perf_counter", return_value=now):
                app.Session._stop_when_idle(session)
        check(104.0)  # nothing heard yet: wait out the rest from the start
        self.assertEqual((stopped, scheduled), ([], [app.IDLE_STOP - 4.0]))
        session.heard_at = 108.0
        check(110.0)  # speech moved the deadline
        self.assertEqual((stopped, scheduled[-1]), ([], app.IDLE_STOP - 2.0))
        state.toggle = False
        check(108.0 + app.IDLE_STOP)  # key still held: push-to-talk never times out
        self.assertEqual(stopped, [])
        state.toggle = True
        check(108.0 + app.IDLE_STOP)
        self.assertEqual(stopped, [True])
        state.recording = None
        count = len(scheduled)
        check(200.0)  # already stopped: the timer chain ends
        self.assertEqual((stopped, len(scheduled)), ([True], count))


@unittest.skipUnless(sys.platform == "win32", "the app imports Windows audio and input")
class EnterEnds(unittest.TestCase):
    def test_enter_ends_dictation_and_blocks_pending_writes(self):
        from thock import app
        live = types.SimpleNamespace(blocked=False)
        session = types.SimpleNamespace(live=live, entered=False, settings={"input_mode": "toggle"})
        session.enter = lambda: app.Session.enter(session)
        stopped = []
        state = types.SimpleNamespace(recording=session, _stop=lambda: stopped.append(True),
                                      settings={"input_mode": "toggle"})
        app.App.on_key(state, "enter")
        self.assertEqual((stopped, session.entered, live.blocked), ([True], True, True))
        state.recording = None
        app.App.on_key(state, "enter")  # Enter without dictation does nothing
        self.assertEqual(stopped, [True])


@unittest.skipUnless(sys.platform == "win32", "the app imports Windows audio and input")
class WaitingGuide(unittest.TestCase):
    def test_waiting_guide_stays_until_a_field_is_picked_and_is_not_an_error(self):
        from thock import app
        live = types.SimpleNamespace(waiting=True)
        session = type("Session", (), {})()  # hashable, like a real session
        session.live = live
        state = types.SimpleNamespace(notice_until=0.0, notice="", last=session, active={session})
        self.assertEqual(app.App.preview(state), app.WAITING)
        self.assertEqual(app.App.status(types.SimpleNamespace(recording=session, toggle=True,
                                                               active={session}, notice_until=0.0)), ("recording", True))
        state.notice_until, state.notice = float("inf"), "입력 위치나 글이 바뀌어 자동 입력을 멈췄습니다."
        self.assertEqual(app.App.preview(state), state.notice)  # a notice still takes the pill
        state.notice_until = 0.0
        live.waiting = False
        self.assertEqual(app.App.preview(state), "")
        live.waiting = True
        state.active = set()
        self.assertEqual(app.App.preview(state), "")  # the dictation ended


@unittest.skipUnless(sys.platform == "win32", "the app imports Windows audio and input")
class PillNotices(unittest.TestCase):
    def state(self):
        from thock import app
        session = type("Session", (), {})()
        session.notices = []
        state = app.App.__new__(app.App)
        state.recording, state.last, state.active = None, session, {session}
        return app, state, session

    def test_a_fault_is_logged_kept_with_its_dictation_and_shown_on_the_black_pill(self):
        app, state, session = self.state()
        with self.assertLogs("voicetype", level="WARNING") as logs:
            state.notify("입력하지 못한 글을 보관했습니다.", action="copy", fault=True)
        self.assertIn("fault notice", logs.output[0])
        self.assertEqual(session.notices, ["입력하지 못한 글을 보관했습니다."])
        state.active = set()
        self.assertEqual(app.App.status(state)[0], "notice")  # no red pill any more
        self.assertEqual(state.notice_action, "copy")  # it stays with its button until pressed
        state.notify("안내만 합니다.")
        self.assertEqual(session.notices, ["입력하지 못한 글을 보관했습니다."])  # not a fault
        self.assertIsNone(state.notice_action)

    def test_copy_on_the_pill_copies_the_kept_text(self):
        app, state, _ = self.state()
        state.active = set()
        state.recovery = [{"id": "old", "text": "a"}, {"id": "new", "text": "b"}]
        state.recovery_action = Mock()
        state.notify("입력하지 못한 글을 보관했습니다.", action="copy")
        state.act_on_notice()
        state.recovery_action.assert_called_once_with("new", "copy")
        self.assertIn("복사했습니다", state.notice)
        self.assertIsNone(state.notice_action)


@unittest.skipUnless(sys.platform == "win32", "the app imports Windows audio and input")
class FlagLastDictation(unittest.TestCase):
    def app(self, consent, record):
        from thock import app
        session = SimpleNamespace(record=record, overflows=0, app="claude.exe", session_id=None,
                                  timing=lambda: {"idle_s": 7200, "first_audio_ms": 1400, "first_text_ms": 2100})
        state = app.App.__new__(app.App)
        state.last, state.recording, state.active = session, None, set()
        state.account = SimpleNamespace(cached={"error_reports": {"enabled": consent}})
        state._queue_error = Mock()
        return app, state

    def test_flag_is_logged_and_reported_with_the_last_timings(self):
        app, state = self.app(True, {"time": "2026-10-01 09:00:00", "input_failure": "delivery_unverified",
                                     "late_ms": 350})
        with self.assertLogs("voicetype", level="WARNING") as logs:
            app.App.flag_last(state)
        self.assertIn("first_audio_ms=1400", logs.output[0])
        stage, code, details = state._queue_error.call_args.args[1:]
        self.assertEqual((stage, code, details["late_ms"], details["idle_s"]),
                         ("user_flag", "delivery_unverified", 350, 7200))
        self.assertLessEqual(len(details), 8)  # the server accepts at most eight details

    def test_flag_stays_local_without_consent_and_waits_for_a_finished_dictation(self):
        app, state = self.app(False, {"time": "2026-10-01 09:00:00"})
        with self.assertLogs("voicetype", level="WARNING"):
            app.App.flag_last(state)
        state._queue_error.assert_not_called()
        self.assertLess(state.notice_until - __import__("time").perf_counter(), 4)  # a short receipt
        state.last.record = None
        app.App.flag_last(state)
        self.assertIn("끝난 뒤", state.notice)


@unittest.skipUnless(sys.platform == "win32", "the app imports Windows audio and input")
class AccountNotice(unittest.TestCase):
    def test_an_account_state_waits_on_the_pill_and_confirm_opens_the_account_window(self):
        from thock import app
        state = app.App.__new__(app.App)
        state.recording, state.active = None, set()
        state.open_welcome = Mock()
        state.notify("이 계정의 Thock 이용권을 확인해 주세요.", action="account")
        self.assertEqual(app.App.status(state)[0], "notice")
        self.assertEqual(state.notice_action, "account")
        state.act_on_notice()
        state.open_welcome.assert_called_once()
        self.assertIsNone(app.App.status(state)[0])

    def test_the_account_message_goes_away_once_the_account_is_ready(self):
        from pathlib import Path
        from thock import app
        state = app.App.__new__(app.App)
        state.recording, state.active = None, set()
        state.settings = {"welcome_complete": True}
        state.data_root = Path("unused")
        state.account = SimpleNamespace(status=lambda force=False: {"state": "offline", "ready": True})
        state.legacy_available = lambda: False
        state.notify("이 계정의 Thock 이용권을 확인해 주세요.", action="account")
        app.App.account_status(state)
        self.assertIsNone(app.App.status(state)[0])


if __name__ == "__main__":
    unittest.main()
