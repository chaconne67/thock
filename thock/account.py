"""AI Shift account for Thock: loopback PKCE sign-in and Windows Credential Manager."""

import base64
import ctypes
import ctypes.wintypes as wt
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SITE = "https://aishift.kr"
CREDENTIAL_NAME = "AIShift/Thock"
MESSAGES = {
    "signed_out": "AI Shift에 다시 로그인해 주세요.",
    "quota_exceeded": "베타 사용 한도에 도달했습니다. 내일 다시 시도해 주세요.",
    "beta_unavailable": "Thock 베타 음성입력이 아직 준비되지 않았습니다.",
    "provider_unavailable": "음성 서비스에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
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


def read_token():
    api = _advapi()
    pointer = ctypes.POINTER(CREDENTIALW)()
    if not api.CredReadW(CREDENTIAL_NAME, 1, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == 1168:
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = pointer.contents
        return ctypes.string_at(entry.CredentialBlob, entry.CredentialBlobSize).decode("utf-8")
    finally:
        api.CredFree(pointer)


def write_token(token):
    api = _advapi()
    blob = ctypes.create_string_buffer(token.encode("utf-8"))
    entry = CREDENTIALW()
    entry.Type = 1  # CRED_TYPE_GENERIC
    entry.TargetName = CREDENTIAL_NAME
    entry.UserName = "Thock"
    entry.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE, scoped to this Windows user
    entry.CredentialBlobSize = len(token.encode("utf-8"))
    entry.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    if not api.CredWriteW(ctypes.byref(entry), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def delete_token():
    api = _advapi()
    if not api.CredDeleteW(CREDENTIAL_NAME, 1, 0) and ctypes.get_last_error() != 1168:
        raise ctypes.WinError(ctypes.get_last_error())


class Account:
    def __init__(self):
        self.token = read_token()
        self.email = ""
        self.pending = None
        self.lock = threading.Lock()
        self.last_error = ""

    @staticmethod
    def _request(path, data=None, token=None, timeout=8):
        headers = {"User-Agent": "Thock/0.2", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            SITE + path, data=json.dumps(data).encode("utf-8") if data is not None else None,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response) if response.status != 204 else {}
        except urllib.error.HTTPError as error:
            try:
                code = json.load(error).get("error", "account_unreachable")
            except (ValueError, OSError):
                code = "account_unreachable"
            raise AccountError(code) from None
        except (OSError, ValueError):
            raise AccountError("account_unreachable") from None

    def begin(self, port):
        verifier = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        with self.lock:
            self.pending = (state, verifier, time.monotonic() + 300)
            self.last_error = ""
        query = urllib.parse.urlencode({"port": port, "state": state, "challenge": challenge})
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
        self.email = response.get("email", "")
        self.last_error = ""
        return self.email

    def status(self):
        if self.pending and time.monotonic() > self.pending[2]:
            self.pending = None
            self.last_error = "로그인 시간이 지났습니다. 다시 시도해 주세요."
        if not self.token:
            return {"state": "waiting" if self.pending else "signed_out",
                    "email": "", "error": self.last_error}
        try:
            body = self._request("/api/app/me", token=self.token)
        except AccountError as error:
            if error.code == "signed_out":
                delete_token()
                self.token = None
                self.email = ""
                return {"state": "signed_out", "email": "", "error": error.code}
            return {"state": "offline", "email": self.email, "error": error.code}
        self.email = body.get("email", "")
        return {"state": "signed_in", "email": self.email, "beta_ready": body.get("beta_ready", False),
                "error": self.last_error}

    def logout(self):
        if self.token:
            try:
                self._request("/api/app/logout", {}, token=self.token)
            except AccountError as error:
                if error.code != "signed_out":
                    raise
        delete_token()
        self.token = None
        self.email = ""

    def _authorized(self, path, payload, timeout=8):
        if not self.token:
            raise AccountError("signed_out")
        try:
            return self._request(path, payload, token=self.token, timeout=timeout)
        except AccountError as error:
            if error.code == "signed_out":
                delete_token()
                self.token = None
                self.email = ""
            raise

    def session_key(self):
        return self._authorized("/api/thock/session", {})["api_key"]

    def complete(self, kind, payload):
        return self._authorized("/api/thock/complete", {"kind": kind, **payload}, timeout=17)["text"]
