"""Direct correction with this PC's own key, key renewal and error-report gating. No network is used."""
import sys
import time
import types
import unittest
from unittest.mock import Mock, patch


def json_text(text):
    import json
    return json.dumps(text)[1:-1]


class Response:
    def __init__(self, status, body=b""):
        self.status, self.body = status, body

    def read(self):
        return self.body


class DirectCorrection(unittest.TestCase):
    def polisher(self, statuses):
        from thock.correction import Polisher
        account = types.SimpleNamespace(correction_key=Mock(side_effect=lambda force=False: {
            "api_key": "renewed" if force else "cached", "model": "openai/gpt-6-luna", "expires": 0}))
        profile = types.SimpleNamespace(terms=lambda: [], summary=lambda: "")
        notes = types.SimpleNamespace(hint=lambda: "", terms=lambda: ["FundKeeper"])
        polisher = Polisher({"terms": ["Thock"], "polish_level": "clean", "style": "none",
                             "emoji": "none"},
                            notes, account, profile)
        conn = Mock()
        replies = iter(statuses)
        conn.getresponse.side_effect = lambda: next(replies)
        return polisher, account, conn

    def test_a_refused_key_is_renewed_once_and_the_text_is_sent_to_openrouter_only(self):
        body = b'{"choices": [{"message": {"content": "\\ub2e4\\ub4ec\\uc740 \\uae00."}}]}'
        polisher, account, conn = self.polisher([Response(401), Response(200, body)])
        with patch("thock.correction.http.client.HTTPSConnection", return_value=conn) as opened:
            self.assertEqual(polisher.correct("다듬을 글"), "다듬은 글.")  # the model answer
        opened.assert_called_once_with("openrouter.ai", timeout=8)
        self.assertEqual([c.kwargs for c in account.correction_key.call_args_list], [{"force": False}, {"force": True}])
        headers = conn.request.call_args.args[3]
        self.assertEqual(headers["Authorization"], "Bearer renewed")
        self.assertIn("<dictation>", conn.request.call_args.args[2])
        self.assertIn("Thock, FundKeeper", conn.request.call_args.args[2])  # the terms the editor spells by

    def test_profile_building_does_not_hold_up_dictation_corrections(self):
        import threading
        body = b'{"choices": [{"message": {"content": "ok"}}]}'
        polisher, _, conn = self.polisher([Response(200, body)] * 2)
        release, started = threading.Event(), threading.Event()
        slow = Mock()
        slow.getresponse.side_effect = lambda: (started.set(), release.wait(2), Response(200, body))[2]
        with patch("thock.correction.http.client.HTTPSConnection", side_effect=[slow, conn]):
            profile = threading.Thread(target=polisher.complete, args=("p", "[]"), kwargs={"background": True})
            profile.start()
            started.wait(2)
            self.assertEqual(polisher.complete("i", "u"), "ok")  # does not wait for the profile
            release.set()
            profile.join(2)

    def test_the_chosen_level_decides_which_instructions_the_editor_gets(self):
        from thock.correction import POLISH_RULES
        body = b'{"choices": [{"message": {"content": "ok"}}]}'
        for level, (rules, _) in POLISH_RULES.items():
            polisher, _, conn = self.polisher([Response(200, body)])
            polisher.settings["polish_level"] = level
            with patch("thock.correction.http.client.HTTPSConnection", return_value=conn):
                polisher.correct("다듬을 글")
            sent = conn.request.call_args.args[2]
            for other, (other_rules, _) in POLISH_RULES.items():
                self.assertEqual(json_text(other_rules) in sent, other == level, (level, other))

    def test_the_writer_gets_the_chosen_style(self):
        from thock.correction import STYLES
        body = b'{"choices": [{"message": {"content": "- \\ub2e4\\ub4ec\\uc74c"}}]}'
        for style, wanted in (("bullets", STYLES["bullets"]), ("casual", STYLES["casual"])):
            polisher, _, conn = self.polisher([Response(200, body)])
            polisher.settings.update(style=style)
            with patch("thock.correction.http.client.HTTPSConnection", return_value=conn):
                self.assertEqual(polisher.restyle("다듬을 글"), "- 다듬음")
            self.assertIn(json_text(wanted), conn.request.call_args.args[2])
        polisher, _, _ = self.polisher([])
        polisher.settings["style"] = "email"
        polisher.complete = lambda *args, **kwargs: ""
        with self.assertRaises(RuntimeError):
            polisher.restyle("다듬을 글")

    def test_jev_picks_go_right_after_their_sentences_at_the_chosen_level(self):
        import tempfile
        from pathlib import Path
        answers = {"s0": {"choice": "🤔", "confidence": 0.9, "probabilities": {"🤔": 0.9, "none": 0.1}},
                   "s1": {"choice": "🤩", "confidence": 0.5, "probabilities": {"🤩": 0.5, "🤔": 0.3, "none": 0.2}},
                   "s2": {"choice": "none", "confidence": 0.8, "probabilities": {"none": 0.8, "💸": 0.15, "📄": 0.05}}}
        text = "되나? 짱이다! 4.5원이다.\n- 끝"
        with tempfile.TemporaryDirectory() as temp, patch("thock.correction.HOME", Path(temp)):
            polisher, _, _ = self.polisher([])
            asked = []
            polisher._jev = lambda key, sentences: asked.append(sentences) or dict(answers, s3=answers["s2"])
            polisher.settings["emoji"] = "some"
            self.assertEqual(polisher.emojify(text), text)  # no key on this PC: the text goes in as it is
            (Path(temp) / "typesafe.key").write_text("k")
            self.assertEqual(polisher.emojify(text), "되나? 🤔 짱이다! 4.5원이다.\n- 끝")
            self.assertEqual(asked[-1], ["되나?", "짱이다!", "4.5원이다.", "- 끝"])
            polisher.settings["emoji"] = "lots"
            self.assertEqual(polisher.emojify(text), "되나? 🤔 짱이다! 🤩 4.5원이다. 💸\n- 끝 💸")
            polisher.settings["emoji"] = "none"
            self.assertEqual(polisher.emojify(text), text)
            polisher.settings["emoji"] = "lots"
            polisher._jev = Mock(side_effect=RuntimeError("typesafe 500"))
            self.assertEqual(polisher.emojify(text), text)  # Jev failing leaves the dictation without emoji

    def test_luna_is_not_told_about_emoji(self):
        body = b'{"choices": [{"message": {"content": "ok"}}]}'
        for style in ("none", "gyeongsang"):
            polisher, _, conn = self.polisher([Response(200, body)])
            polisher.settings.update(emoji="lots", style=style)
            polisher.emojify = lambda text: text
            with patch("thock.correction.http.client.HTTPSConnection", return_value=conn):
                (polisher.correct if style == "none" else polisher.restyle)("다듬을 글")
            self.assertNotIn(json_text("이모지"), conn.request.call_args.args[2], style)

    def test_a_term_given_with_its_sound_reaches_the_editor_and_recognition_both_ways(self):
        from thock.learning import Profile
        polisher, _, _ = self.polisher([])
        polisher.settings["terms"] = ["exdigm = 엑스딤", "Thock"]
        self.assertEqual(polisher._terms()[:2], ["exdigm (소리: 엑스딤)", "Thock"])
        profile = Profile.__new__(Profile)
        profile.data = {}
        profile.terms = lambda: []
        self.assertEqual(Profile.context(profile, "app.exe", ["exdigm = 엑스딤", "Thock"])["terms"],
                         ["exdigm", "엑스딤", "Thock"])

    def test_an_empty_or_cut_off_answer_is_an_error_not_a_correction(self):
        polisher, _, _ = self.polisher([])
        polisher.correct = lambda text: ""
        with self.assertRaises(RuntimeError):
            polisher.polish("노트북 화면 전체에 보이니까", "claude.exe")
        polisher.correct = lambda text: "노트북 화면 전체에 보이니까."
        self.assertEqual(polisher.polish("노트북 화면 전체에 보이니까", "claude.exe"), "노트북 화면 전체에 보이니까.")
        body = b'{"choices": [{"finish_reason": "length", "message": {"content": "\\ub2e4"}}]}'
        polisher, _, conn = self.polisher([Response(200, body)])
        with patch("thock.correction.http.client.HTTPSConnection", return_value=conn):
            with self.assertRaises(RuntimeError):
                polisher.correct("다듬을 글")

    def test_a_second_refusal_is_reported_not_retried_forever(self):
        from thock.correction import KeyRefused
        polisher, account, conn = self.polisher([Response(402), Response(403)])
        with patch("thock.correction.http.client.HTTPSConnection", return_value=conn):
            with self.assertRaises(KeyRefused):
                polisher.polish("다듬을 글", "claude.exe")
        self.assertEqual(account.correction_key.call_count, 2)


@unittest.skipUnless(sys.platform == "win32", "Windows Credential Manager API")
class CorrectionKey(unittest.TestCase):
    def test_key_is_kept_until_near_expiry_and_stored_under_its_own_name(self):
        from thock.account import Account
        with patch("thock.account.read_token", return_value=None):
            account = Account()
        account.token = "app-token"
        issued = {"api_key": "sk-or-v1-x", "model": "openai/gpt-6-luna",
                  "expires_at": "2099-01-01T00:00:00+00:00"}
        with patch.object(account, "_request", return_value=issued) as server, \
                patch("thock.account.write_token") as stored:
            first = account.correction_key()
            second = account.correction_key()
            renewed = account.correction_key(force=True)
        self.assertEqual(server.call_count, 2)
        self.assertEqual(first, second)
        self.assertEqual(renewed["api_key"], "sk-or-v1-x")
        self.assertEqual(stored.call_args.args[1], "AIShift/Thock/Correction")
        account.key["expires"] = time.time() + 60  # about to expire: asks again
        with patch.object(account, "_request", return_value=issued) as server, patch("thock.account.write_token"):
            account.correction_key()
        server.assert_called_once()


@unittest.skipUnless(sys.platform == "win32", "the app imports Windows audio and input")
class ErrorReports(unittest.TestCase):
    def app(self, enabled):
        from thock import app
        state = app.App.__new__(app.App)
        state.account = types.SimpleNamespace(cached={"error_reports": {"enabled": enabled}}, send_error=Mock())
        return state

    def session(self):
        return types.SimpleNamespace(started=time.perf_counter() - 3, heard_at=None, overflows=2,
                                     app="claude.exe", session_id="s")

    def test_only_allowed_app_faults_are_reported_without_any_text(self):
        from thock import app
        session = self.session()
        record = {"input_failure": "delivery_unverified", "text": "말한 내용", "raw": "말한 내용"}
        sent = []
        with patch("thock.app.threading.Thread", side_effect=lambda target, args, daemon: types.SimpleNamespace(
                start=lambda: sent.append(args[0]))):
            app.App.report_error(self.app(False), session, record)
            app.App.report_error(self.app(True), session, {"error": "time_exhausted"})
            app.App.report_error(self.app(True), session, record)
        self.assertEqual(len(sent), 1)
        report = sent[0]
        self.assertEqual((report["stage"], report["code"], report["target_app"]),
                         ("delivery", "delivery_unverified", "claude.exe"))
        self.assertNotIn("말한 내용", repr(report))


if __name__ == "__main__":
    unittest.main()
