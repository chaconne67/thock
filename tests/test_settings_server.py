import json
import urllib.error
import urllib.request
import unittest
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from thock.settings_server import SettingsServer

class LocalScreens(unittest.TestCase):
    def setUp(self):
        self.app=SimpleNamespace(
            public_settings=lambda: {"input_mode":"hold"},
            account_status=lambda **kw:{"state":"signed_out","welcome_complete":False},
            complete_welcome=Mock(), recovery=[], data_root=None, data_lock=threading.RLock(), active=set(),
            profile=SimpleNamespace(building=False))
        self.server=SettingsServer(self.app)
        self.base=f"http://127.0.0.1:{self.server.httpd.server_port}"

    def tearDown(self):
        self.server.httpd.shutdown()
        self.server.httpd.server_close()

    def request(self,path,body=None,token=True):
        req=urllib.request.Request(self.base+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Token":self.server.token if token else "wrong","Content-Type":"application/json"})
        return urllib.request.urlopen(req,timeout=2)

    def test_settings_and_welcome_are_separate_real_routes(self):
        settings=self.request("/").read().decode()
        welcome=self.request("/welcome").read().decode()
        self.assertIn("Thock 설정",settings)
        self.assertNotIn('id="try"',settings)
        self.assertNotIn("첫 받아쓰기 체험",settings)
        self.assertNotIn("Google로 시작하기",settings)
        self.assertIn("Google로 시작하기",welcome)
        self.assertNotIn("Thock 사용 시작",welcome)  # the welcome finishes by itself after sign-in
        self.assertIn("말한 내용과 음성은 보내지 않으며",welcome)  # error-report notice before sign-in
        self.assertIn("오류 정보 보내기",settings)
        self.assertIn("문장 다듬기",settings)
        self.assertNotIn("Soniox",settings+welcome)

    def test_only_the_newest_local_window_stays_open(self):
        from unittest.mock import patch
        with patch("thock.settings_server.subprocess.Popen"), patch("thock.settings_server.os.startfile", create=True):
            self.server.open()
            self.server.open_welcome()
        page = urllib.request.urlopen(self.base + f"/welcome?t={self.server.token}&w=1", timeout=2).read().decode()
        self.assertIn("if(d.window!==1)window.close()", page)
        self.assertEqual(json.load(self.request("/api/window/current"))["window"], 2)

    def test_local_routes_require_instance_token(self):
        for path in ("/","/welcome","/recovery","/api/settings"):
            with self.assertRaises(urllib.error.HTTPError) as error:self.request(path,token=False)
            self.assertEqual(error.exception.code,403)

    def test_completion_does_not_require_sample_dictation(self):
        result=json.load(self.request("/api/welcome/complete",{"import_legacy":False}))
        self.assertTrue(result["ready"])
        self.app.complete_welcome.assert_called_once_with(bring_legacy=False)

    def test_invite_code_is_redeemed_and_refusals_are_shown(self):
        from thock.account import AccountError
        welcome = self.request("/welcome").read().decode()
        self.assertIn("초대 코드", welcome)
        self.app.account = SimpleNamespace(redeem=Mock())
        self.assertEqual(json.load(self.request("/api/account/invite", {"code": "THK-AAAA-BBBB"}))["state"],
                         "signed_out")
        self.app.account.redeem.assert_called_once_with("THK-AAAA-BBBB")
        self.app.account.redeem.side_effect = AccountError("code_used")
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/account/invite", {"code": "THK-AAAA-BBBB"})
        self.assertEqual(json.load(error.exception)["message"], "이미 사용한 초대 코드입니다.")

    def test_sign_in_tab_shows_the_result_and_closes_itself(self):
        from thock.account import AccountError
        self.app.account = SimpleNamespace(finish=Mock(), last_error="")
        self.app.account_status = lambda **kw: {"state": "signed_in", "ready": True}
        page = urllib.request.urlopen(self.base + "/callback?code=c&state=s", timeout=2).read().decode()
        self.assertIn("Thock에 연결됐습니다", page)
        self.assertIn("CapsLock을 누르고", page)
        self.assertIn("window.close()", page)
        self.app.account.finish.side_effect = AccountError("sign_in")
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.base + "/callback?code=c&state=s", timeout=2)
        failed = error.exception.read().decode()
        self.assertIn("연결하지 못했습니다", failed)
        self.assertIn("if(false)", failed)  # a failure stays on screen

    def test_signed_out_personal_data_writes_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/notes",{"action":"add","old":"a","new":"b"})
        self.assertEqual(error.exception.code,401)

    def test_previous_account_screen_cannot_write_new_account_terms(self):
        self.app.data_root = Path("accounts/new-account")
        self.app.update_settings = Mock()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/settings", {"personal_key": "old-account", "terms": ["old words"]})
        self.assertEqual(error.exception.code, 409)
        self.app.update_settings.assert_not_called()

    def test_current_account_screen_can_save(self):
        self.app.data_root = Path("accounts/current-account")
        self.app.update_settings = Mock(return_value={"saved": True})
        result = json.load(self.request("/api/settings", {"personal_key": "current-account", "terms": ["my word"]}))
        self.assertTrue(result["saved"])
        self.app.update_settings.assert_called_once()


class InsideCrema(unittest.TestCase):
    def test_crema_mic_hands_a_press_to_the_app(self):
        loop = Mock()
        app = SimpleNamespace(public_settings=lambda: {}, account_status=lambda **kw: {}, recovery=[], data_root=None,
                              data_lock=threading.RLock(), active=set(), profile=SimpleNamespace(building=False),
                              loop=loop, dictate=Mock())
        server = SettingsServer(app)
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.httpd.server_port}/api/dictate", data=b"{}",
                                             headers={"X-Token": server.token, "Content-Type": "application/json"})
            self.assertEqual(urllib.request.urlopen(request, timeout=2).status, 202)
            loop.call_soon_threadsafe.assert_called_once_with(app.dictate)
        finally:
            server.httpd.shutdown()
            server.httpd.server_close()

    def test_settings_inside_crema_open_crema(self):
        from unittest.mock import patch
        app = SimpleNamespace(public_settings=lambda: {}, account_status=lambda **kw: {}, recovery=[], data_root=None,
                              data_lock=threading.RLock(), active=set(), profile=SimpleNamespace(building=False))
        server = SettingsServer(app)
        try:
            with patch("thock.settings_server.EMBEDDED", True), patch.dict("os.environ", {"THOCK_HOST_EXE": "C:/Crema/app.exe"}), \
                    patch("thock.settings_server.subprocess.Popen") as popen:
                server.open()
                server.open_welcome()
            self.assertEqual([c.args[0] for c in popen.call_args_list], [["C:/Crema/app.exe", "--voice-settings"]] * 2)
        finally:
            server.httpd.shutdown()
            server.httpd.server_close()
