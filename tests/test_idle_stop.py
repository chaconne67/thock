"""A tap-started dictation ends by itself after speech stops."""
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


if __name__ == "__main__":
    unittest.main()
