"""Dictation also ends without a second hotkey press: after silence, or when Enter sends it."""
import sys
import types
import unittest
from unittest.mock import patch


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
        session = types.SimpleNamespace(live=live, entered=False)
        session.enter = lambda: app.Session.enter(session)
        stopped = []
        state = types.SimpleNamespace(recording=session, _stop=lambda: stopped.append(True))
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
        state = types.SimpleNamespace(error_until=0.0, notice="", last=session, active={session})
        self.assertEqual(app.App.preview(state), app.WAITING)
        self.assertEqual(app.App.status(types.SimpleNamespace(error_until=0.0, recording=session, toggle=True,
                                                               active={session})), ("recording", True))
        state.error_until, state.notice = float("inf"), "입력 위치나 글이 바뀌어 자동 입력을 멈췄습니다."
        self.assertEqual(app.App.preview(state), state.notice)  # a real failure still takes the pill
        state.error_until = 0.0
        live.waiting = False
        self.assertEqual(app.App.preview(state), "")
        live.waiting = True
        state.active = set()
        self.assertEqual(app.App.preview(state), "")  # the dictation ended


if __name__ == "__main__":
    unittest.main()
