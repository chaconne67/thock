"""Direct correction with this PC's own key, key renewal and error-report gating. No network is used."""
import sys
import time
import types
import unittest
from unittest.mock import Mock, patch


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
        polisher = Polisher({"terms": ["Thock"]}, notes, account, profile)
        conn = Mock()
        replies = iter(statuses)
        conn.getresponse.side_effect = lambda: next(replies)
        return polisher, account, conn

    def test_a_refused_key_is_renewed_once_and_the_text_is_sent_to_openrouter_only(self):
        body = b'{"choices": [{"message": {"content": "\\ub2e4\\ub4ec\\uc740 \\uae00."}}]}'
        polisher, account, conn = self.polisher([Response(401), Response(200, body)])
        with patch("thock.correction.http.client.HTTPSConnection", return_value=conn) as opened:
            self.assertEqual(polisher.correct("다듬을 글"), "다듬은 글.")  # the model answer; polish() then checks it
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

    def test_the_editor_may_fix_a_misheard_word_but_never_drop_or_add_one(self):
        from thock.correction import kept_words
        for heard, edited in (
                ("이건 내가 볼게 너는 돌려 줄래", "이건 내가 볼게. 너는 돌려 줄래?"),
                ("확인해볼게 가나다 순으로", "확인해 볼게. 가나다순으로."),  # spacing
                ("um so we ship it", "Um, so we ship it."),  # letter case
                ("웹 화면이 대화의 삽입돼서", "웹 화면이 대화에 삽입돼서."),  # a misheard word
                ("SOAP 프로그램을 개발하면서", "쏙 프로그램을 개발하면서"),  # a term in the other alphabet
                ("지피티 오디오 미니", "GPT 오디오 미니"),
                ("펀드 키퍼 저장소", "FundKeeper 저장소"),
                ("음 그러니까 커밋해줘 어", "그러니까 커밋해 줘."),  # hesitation sounds may go
                ("um so we uh ship it", "So we ship it.")):
            self.assertTrue(kept_words(heard, edited), edited)
        for heard, edited in (
                ("노트북 화면 전체에 보이니까", "노트북 화면이 보이니까."),  # a word dropped
                ("가 가 가나다 순으로", "가나다순으로."),  # a repetition dropped
                ("어디 있어", "디 있어?"),  # a hesitation sound only as a word of its own
                ("이건 테스트", "이건 테스트 문장이야."),  # words added
                ("질문에 답해 줘 대한민국의 수도는 어디야", "서울입니다."),  # answered
                ("오늘 날씨 어때", "오늘은 맑아요."),
                ("오늘 날씨 어때", "It's sunny today."),
                ("농담 해 줘", "Why did the cat sit on the computer?")):
            self.assertFalse(kept_words(heard, edited), edited)
        polisher, _, _ = self.polisher([])
        polisher.correct = lambda text: "노트북 화면이 보이니까."
        self.assertIsNone(polisher.polish("노트북 화면 전체에 보이니까", "claude.exe"))
        polisher.correct = lambda text: "노트북 화면 전체에 보이니까."
        self.assertEqual(polisher.polish("노트북 화면 전체에 보이니까", "claude.exe"), "노트북 화면 전체에 보이니까.")

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
