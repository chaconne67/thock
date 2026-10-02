"""AI Shift account for Thock: loopback PKCE sign-in and Windows Credential Manager."""

import base64
import ctypes
import ctypes.wintypes as wt
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime

from .config import EMBEDDED, VERSION

SITE = os.environ.get("THOCK_SITE") or "https://thock.cloud"  # Thock's own address for AI Shift accounts
CREDENTIAL_NAME = os.environ.get("THOCK_CREDENTIAL") or "AIShift/Thock"
# A server deploy leaves it unreachable for seconds: a request that never reached Thock is sent again meanwhile.
RETRY_SECONDS = 3
UNREACHED = {"account_unreachable", "server_unavailable"}
MESSAGES = {
    "signed_out": "AI Shift에 다시 로그인해 주세요.",
    "access_unavailable": "이 계정의 Thock 이용권을 확인해 주세요.",
    "access_suspended": "Thock 이용이 중지되었습니다. 내 계정에서 확인해 주세요.",
    "access_not_started": "이용 시작일 전입니다. 내 계정에서 기간을 확인해 주세요.",
    "access_expired": "이용 기간이 끝났습니다. 내 계정에서 확인해 주세요.",
    "time_exhausted": "제공 시간이 모두 사용되었습니다. 내 계정에서 확인해 주세요.",
    "budget_exhausted": "무료 베타의 운영 한도에 도달해 잠시 쉬고 있습니다.",
    "session_busy": "진행 중인 받아쓰기가 끝나면 다시 시작해 주세요.",
    "beta_unavailable": "Thock 서비스를 준비 중입니다. 잠시 뒤 연결을 확인해 주세요.",
    "provider_unavailable": "음성 서비스에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
    "invalid_completion": "문장을 다듬지 못했습니다. 인식한 원문을 확인해 주세요.",
    "learning_paused": "자동으로 배우기가 꺼져 있습니다.",
    "profile_not_due": "새 받아쓰기가 더 쌓이면 다시 파악할 수 있습니다.",
    "already_processed": "이미 처리한 요청입니다.",
    "session_expired": "처리 시간이 지났습니다. 새 받아쓰기를 시작해 주세요.",
    "account_unreachable": "인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
    "server_unavailable": "Thock 서버가 잠시 응답하지 않습니다. 잠시 뒤 다시 시도해 주세요.",
    "correction_limit_reached": "이번 달 문장 다듬기 한도를 모두 사용했습니다. 인식한 원문을 입력합니다.",
    "key_busy": "문장 다듬기 연결을 잠시 뒤 다시 준비합니다.",
    "sign_in": "로그인을 완료하지 못했습니다. 다시 연결해 주세요.",
    "unknown_invite": "초대 코드를 찾지 못했습니다. 받은 코드를 다시 확인해 주세요.",
    "code_used": "이미 사용한 초대 코드입니다.",
    "code_expired": "사용 기한이 지난 초대 코드입니다. 새 코드를 요청해 주세요.",
    "access_exists": "이 계정에는 이미 Thock 이용권이 있습니다.",
    "too_many_attempts": "잘못된 코드를 여러 번 입력했습니다. 한 시간 뒤 다시 시도해 주세요.",
}


class AccountError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES.get(code, "AI Shift 연결을 확인해 주세요."))


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR), ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR), ("UserName", wt.LPWSTR),
    ]


def _advapi():
    api = ctypes.WinDLL("Advapi32", use_last_error=True)
    api.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    api.CredReadW.restype = wt.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wt.DWORD]
    api.CredWriteW.restype = wt.BOOL
    api.CredDeleteW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]
    api.CredDeleteW.restype = wt.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    return api


def blob_text(blob):
    """A stored sign-in as text: Thock's own is UTF-8, which never holds a zero byte; one stored by Rust's keyring
    (Crema's, read inside Crema) is UTF-16."""
    return blob.decode("utf-16-le") if b"\x00" in blob else blob.decode("utf-8")


def read_token(name=None):
    name = name or CREDENTIAL_NAME  # looked up per call so tests can isolate it
    api = _advapi()
    pointer = ctypes.POINTER(CREDENTIALW)()
    if not api.CredReadW(name, 1, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == 1168:
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = pointer.contents
        return blob_text(ctypes.string_at(entry.CredentialBlob, entry.CredentialBlobSize))
    finally:
        api.CredFree(pointer)


def write_token(token, name=None):
    name = name or CREDENTIAL_NAME
    api = _advapi()
    blob = ctypes.create_string_buffer(token.encode("utf-8"))
    entry = CREDENTIALW()
    entry.Type = 1  # CRED_TYPE_GENERIC
    entry.TargetName = name
    entry.UserName = "Thock"
    entry.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE, scoped to this Windows user
    entry.CredentialBlobSize = len(token.encode("utf-8"))
    entry.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    if not api.CredWriteW(ctypes.byref(entry), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def delete_token(name=None):
    name = name or CREDENTIAL_NAME
    api = _advapi()
    if not api.CredDeleteW(name, 1, 0) and ctypes.get_last_error() != 1168:
        raise ctypes.WinError(ctypes.get_last_error())


class Account:
    def __init__(self):
        self.token = read_token()
        self.email = ""
        self.pending = None
        self.lock = threading.Lock()
        self.last_error = ""
        self.cached = {"state": "checking" if self.token else "signed_out", "email": "", "ready": False}
        self.checked_at = 0.0
        self.key_lock = threading.Lock()
        try:
            self.key = json.loads(read_token(CREDENTIAL_NAME + "/Correction") or "null") if self.token else None
        except (OSError, ValueError):
            self.key = None

    @staticmethod
    def _request(path, data=None, token=None, timeout=8):
        headers = {"User-Agent": f"Thock/{VERSION}", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            SITE + path, data=json.dumps(data).encode("utf-8") if data is not None else None,
            headers=headers,
        )
        started = time.monotonic()
        while True:
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.load(response) if response.status != 204 else {}
            except urllib.error.HTTPError as error:
                try:
                    code = json.load(error).get("error", "account_unreachable")
                except (ValueError, OSError):  # not Thock's answer: the server in front could not reach Thock
                    code = "server_unavailable" if error.code in (502, 503) else "account_unreachable"
            except OSError:  # no connection: the internet, or the server not answering
                code = "account_unreachable"
            except ValueError:
                raise AccountError("account_unreachable") from None
            if code not in UNREACHED or time.monotonic() - started > RETRY_SECONDS:  # a timeout has waited enough
                raise AccountError(code) from None
            time.sleep(0.5)

    def begin(self, port, switch=False):
        """The sign-in address. A browser already signed in connects that account; switch asks Google which."""
        verifier = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        with self.lock:
            self.pending = (state, verifier, time.monotonic() + 300)
            self.last_error = ""
        query = urllib.parse.urlencode({"port": port, "state": state, "challenge": challenge, **({"switch": 1} if switch else {})})
        return SITE + "/app/login/?" + query

    def finish(self, code, state):
        with self.lock:
            pending = self.pending
            if not pending or time.monotonic() > pending[2] or not hmac.compare_digest(state, pending[0]):
                raise AccountError("sign_in")
            verifier = pending[1]
            self.pending = None
        response = self._request("/api/app/token", {"code": code, "verifier": verifier})
        token = response.get("token")
        if not isinstance(token, str) or not token:
            raise AccountError("sign_in")
        try:
            write_token(token)
        except OSError:
            try:
                self._request("/api/app/logout", {}, token=token)
            except AccountError:
                pass
            raise
        self.token = token
        self.checked_at = 0
        self.email = response.get("email", "")
        self.last_error = ""
        return self.email

    def status(self, force=False):
        if self.pending and time.monotonic() > self.pending[2]:
            self.pending = None
            self.last_error = "로그인 시간이 지났습니다. 다시 시도해 주세요."
        if not self.token:
            self.cached = {"state": "waiting" if self.pending else "signed_out",
                           "email": "", "error": self.last_error, "ready": False}
            return self.cached
        if not force and time.monotonic() - self.checked_at < 15:
            return self.cached
        token = self.token
        try:
            body = self._request("/api/app/me", token=token)
        except AccountError as error:
            if error.code == "signed_out":
                self._clear(token)
                self.cached = {"state": "signed_out", "email": "", "ready": False, "error": str(error)}
            else:
                self.cached = {**self.cached, "state": "offline", "ready": False, "error": str(error)}
        else:
            if token != self.token:
                return self.cached
            self.email = body.get("email", "")
            access = body.get("access", {})
            ready = bool(body.get("beta_ready") and access.get("allowed"))
            reason = ("beta_unavailable" if not body.get("beta_ready") else access.get("reason", ""))
            self.cached = {"state": "signed_in", "email": self.email, "account_id": body.get("account_id"),
                           "beta_ready": body.get("beta_ready", False), "ready": ready, "access": access,
                           "error_reports": body.get("error_reports") or {}, "error": MESSAGES.get(reason, "")}
        self.checked_at = time.monotonic()
        return self.cached

    def _clear(self, token):
        with self.lock:
            if token == self.token:
                if not EMBEDDED:  # inside Crema the sign-in is Crema's, which signs out itself
                    delete_token()
                delete_token(CREDENTIAL_NAME + "/Correction")  # the server switches the key off as well
                self.key = None
                self.token = None
                self.email = ""
                self.checked_at = 0

    def logout(self):
        if self.token:
            try:
                self._request("/api/app/logout", {}, token=self.token)
            except AccountError as error:
                if error.code != "signed_out":
                    raise
        self._clear(self.token)
        self.pending = None
        self.cached = {"state": "signed_out", "email": "", "ready": False}

    def _authorized(self, path, payload, timeout=8):
        if not self.token:
            raise AccountError("signed_out")
        token = self.token
        try:
            return self._request(path, payload, token=token, timeout=timeout)
        except AccountError as error:
            if error.code == "signed_out":
                self._clear(token)
            self.last_error = str(error)
            raise

    def start_session(self):
        response = self._authorized("/api/thock/session", {"request_id": str(uuid.uuid4())}, timeout=10)
        try:
            uuid.UUID(response["session_id"])
            if (not isinstance(response["api_key"], str) or not response["api_key"]
                    or isinstance(response["max_session_seconds"], bool)
                    or not 1 <= response["max_session_seconds"] <= 300):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise AccountError("provider_unavailable") from None
        self.last_error = ""
        return response

    def report(self, session_id, recorded_ms, outcome, **metrics):
        return self._authorized("/api/thock/finish", {
            "session_id": session_id, "recorded_ms": recorded_ms, "outcome": outcome, **metrics})



    def correction_key(self, force=False):
        """This device's own limited OpenRouter key and model; renewed when refused or about to expire."""
        with self.key_lock:
            key = self.key
            if force or not key or key.get("expires", 0) - time.time() < 3600:
                response = self._authorized("/api/thock/key", {}, timeout=15)
                try:
                    if not (isinstance(response["api_key"], str) and response["api_key"]
                            and isinstance(response["model"], str) and response["model"]):
                        raise ValueError
                    expires = datetime.fromisoformat(response["expires_at"]).timestamp()
                except (KeyError, TypeError, ValueError):
                    raise AccountError("provider_unavailable") from None
                key = {"api_key": response["api_key"], "model": response["model"], "expires": expires}
                try:
                    write_token(json.dumps(key), CREDENTIAL_NAME + "/Correction")
                except OSError:
                    pass  # still usable for this run; the next start asks the server again
                self.key = key
            return key

    def redeem(self, code):
        """Turn a staff or partner invite code into this account's Thock access."""
        if not isinstance(code, str) or not code.strip():
            raise AccountError("unknown_invite")
        try:
            self._authorized("/api/thock/invite", {"code": code.strip()[:32]})
        except AccountError as error:
            if error.code == "invalid_code":  # the sign-in exchange uses the same word for its own codes
                raise AccountError("unknown_invite") from None
            raise
        self.checked_at = 0  # the next status shows the new access

    def set_error_reports(self, enabled):
        return self._authorized("/api/thock/consent", {"enabled": bool(enabled)})

    def send_error(self, report):
        return self._authorized("/api/thock/errors", report, timeout=5)
