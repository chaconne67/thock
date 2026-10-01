"""AI Shift desktop sign-in and credential handling."""

import hashlib
import sys
import unittest
import urllib.parse
import urllib.request
from unittest.mock import patch

from thock.account import Account, AccountError, blob_text


class AccountFlowTests(unittest.TestCase):
    @patch("thock.account.read_token", return_value=None)
    def test_pkce_callback_saves_only_app_token(self, _read):
        account = Account()
        url = urllib.parse.urlparse(account.begin(43123))
        query = urllib.parse.parse_qs(url.query)
        state = query["state"][0]
        verifier = account.pending[1]
        expected = __import__("base64").urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(url.scheme, "https")
        self.assertEqual(url.hostname, "thock.cloud")
        self.assertEqual(query["challenge"], [expected])
        self.assertNotIn("switch", query)  # a signed-in browser connects without another step
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlparse(account.begin(43123, switch=True)).query)["switch"],
                         ["1"])
        state = urllib.parse.parse_qs(urllib.parse.urlparse(account.begin(43123)).query)["state"][0]
        with patch.object(account, "_request", return_value={"token": "app-token", "email": "x@example.com"}),              patch("thock.account.write_token") as write:
            self.assertEqual(account.finish("one-time-code", state), "x@example.com")
        write.assert_called_once_with("app-token")
        self.assertEqual(account.token, "app-token")
        self.assertIsNone(account.pending)

    @patch("thock.account.read_token", return_value=None)
    def test_wrong_state_does_not_exchange(self, _read):
        account = Account()
        account.begin(43123)
        with patch.object(account, "_request") as request:
            with self.assertRaises(AccountError):
                account.finish("code", "wrong-state")
            request.assert_not_called()

    @patch("thock.account.read_token", return_value=None)
    def test_expired_sign_in_can_restart(self, _read):
        account = Account()
        account.begin(43123)
        account.pending = (*account.pending[:2], 0)
        self.assertEqual(account.status()["state"], "signed_out")
        self.assertIsNone(account.pending)
        self.assertIn("시간", account.last_error)

    @patch("thock.account.read_token", return_value="app-token")
    def test_revoked_app_token_is_removed(self, _read):
        account = Account()
        with patch.object(account, "_request", side_effect=AccountError("signed_out")),              patch("thock.account.delete_token") as delete:
            with self.assertRaises(AccountError):
                account.start_session()
        delete.assert_any_call()  # the app token
        delete.assert_any_call("AIShift/Thock/Correction")  # and this device's correction key
        self.assertIsNone(account.token)


class InviteTests(unittest.TestCase):
    @patch("thock.account.read_token", return_value="app-token")
    def test_invite_refusals_have_their_own_messages(self, _read):
        account = Account()
        with patch.object(account, "_request", return_value={"access": {}}) as request:
            account.redeem(" thk-aaaa-bbbb ")
        self.assertEqual(request.call_args.args[:2], ("/api/thock/invite", {"code": "thk-aaaa-bbbb"}))
        with patch.object(account, "_request", side_effect=AccountError("invalid_code")):
            with self.assertRaises(AccountError) as error:
                account.redeem("THK-AAAA-BBBB")
        self.assertIn("초대 코드를 찾지 못했습니다", str(error.exception))  # not the sign-in message
        with self.assertRaises(AccountError):
            account.redeem("  ")


@unittest.skipUnless(sys.platform == "win32", "Windows Credential Manager")
class WindowsCredentialTests(unittest.TestCase):
    def test_round_trip_under_isolated_test_name(self):
        import secrets
        from thock import account

        target = "AIShift/Thock/Test/" + secrets.token_hex(8)
        with patch.object(account, "CREDENTIAL_NAME", target):
            try:
                self.assertIsNone(account.read_token())
                account.write_token("test-token")
                self.assertEqual(account.read_token(), "test-token")
            finally:
                account.delete_token()


if __name__ == "__main__":
    unittest.main()


class StoredSignInTests(unittest.TestCase):
    def test_reads_thock_utf8_and_crema_utf16_sign_ins(self):
        self.assertEqual(blob_text("tok-123".encode("utf-8")), "tok-123")
        self.assertEqual(blob_text("tok-123".encode("utf-16-le")), "tok-123")
