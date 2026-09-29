import json
import urllib.error
import urllib.request
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from thock.settings_server import SettingsServer

class LocalScreens(unittest.TestCase):
    def setUp(self):
        self.app=SimpleNamespace(
            public_settings=lambda: {"input_mode":"hold"},
            account_status=lambda **kw:{"state":"signed_out","welcome_complete":False},
            complete_welcome=Mock(), recovery=[], data_root=None, active=set(),
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
        self.assertIn("Google로 연결하기",welcome)
        self.assertIn("문장 다듬기",settings)
        self.assertNotIn("Soniox",settings+welcome)

    def test_local_routes_require_instance_token(self):
        for path in ("/","/welcome","/recovery","/api/settings"):
            with self.assertRaises(urllib.error.HTTPError) as error:self.request(path,token=False)
            self.assertEqual(error.exception.code,403)

    def test_completion_does_not_require_sample_dictation(self):
        result=json.load(self.request("/api/welcome/complete",{"import_legacy":False}))
        self.assertTrue(result["ready"])
        self.app.complete_welcome.assert_called_once_with(bring_legacy=False)

    def test_signed_out_personal_data_writes_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/notes",{"action":"add","old":"a","new":"b"})
        self.assertEqual(error.exception.code,401)
