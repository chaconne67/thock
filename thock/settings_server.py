"""Separate settings, welcome and recovery windows served only on this PC."""

import hmac
import http.server
import json
import os
import secrets
import subprocess
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .account import AccountError
from .config import HOME, log
from .sound import SOUNDS


class SettingsServer:
    """The local page uses a per-run token; the Google return uses a separate random state."""

    def __init__(self, app):
        self.app, self.token = app, secrets.token_urlsafe(24)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def open(self):
        self._open("/")

    def open_welcome(self):
        self._open("/welcome")

    def open_recovery(self):
        self._open("/recovery")

    def _open(self, path):
        log.info("local window opened: %s", path)
        url = f"http://127.0.0.1:{self.httpd.server_port}{path}?t={self.token}"
        edge = next((p for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
                                 Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe")
                     if p.exists()), None)
        if edge:
            subprocess.Popen([str(edge), f"--app={url}", "--window-size=540,860", f"--user-data-dir={HOME / 'edge'}",
                              "--no-first-run", "--no-default-browser-check", "--disable-extensions"])
        else:
            os.startfile(url)

    def _handler(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _host_ok(self):
                return self.headers.get("Host") == f"127.0.0.1:{server.httpd.server_port}"

            def _allowed(self):
                token = parse_qs(urlparse(self.path).query).get("t", [""])[0] or self.headers.get("X-Token", "")
                return self._host_ok() and hmac.compare_digest(token, server.token)

            def _send(self, code, body, content_type="application/json; charset=utf-8"):
                data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/callback":
                    if not self._host_ok():
                        return self._send(403, {"error": "forbidden"})
                    query = parse_qs(urlparse(self.path).query)
                    try:
                        code, state = query["code"][0], query["state"][0]
                        server.app.account.finish(code, state)
                        server.app.account_status(force=True)
                    except (KeyError, IndexError, AccountError, OSError):
                        server.app.account.last_error = "Google 로그인을 완료하지 못했습니다. Thock에서 다시 시도해 주세요."
                        page = "<!doctype html><meta charset=utf-8><title>Thock</title><p>로그인을 완료하지 못했습니다. Thock에서 다시 시도해 주세요.</p>"
                        return self._send(400, page.encode(), "text/html; charset=utf-8")
                    page = "<!doctype html><meta charset=utf-8><title>Thock</title><p>Thock에 연결되었습니다. 이 창은 닫아도 됩니다.</p>"
                    return self._send(200, page.encode(), "text/html; charset=utf-8")
                if not self._allowed():
                    return self._send(403, {"error": "forbidden"})
                if path in {"/", "/welcome", "/recovery"}:
                    name = {"/": "settings", "/welcome": "welcome", "/recovery": "recovery"}[path]
                    page = (Path(__file__).parent / (name + ".html")).read_text(encoding="utf-8")
                    return self._send(200, page.replace("__TOKEN__", server.token).encode("utf-8"),
                                      "text/html; charset=utf-8")
                if path == "/api/settings":
                    with server.app.data_lock:
                        return self._send(200, server.app.public_settings())
                if path == "/api/account/status":
                    return self._send(200, server.app.account_status())
                if path == "/api/recovery":
                    return self._send(200, server.app.recovery)
                if path == "/api/sound-preview":
                    keyboard = parse_qs(urlparse(self.path).query).get("keyboard", [""])[0]
                    if keyboard not in SOUNDS:
                        return self._send(404, {"error": "not found"})
                    audio = Path(__file__).parent / "sounds" / SOUNDS[keyboard]["processing"]
                    return self._send(200, audio.read_bytes(), "audio/wav")
                self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._allowed():
                    return self._send(403, {"error": "forbidden"})
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 <= length <= 20000:
                        raise ValueError
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError
                except (ValueError, TypeError):
                    return self._send(400, {"error": "bad_request"})
                path = urlparse(self.path).path
                with server.app.data_lock:
                    personal_write = path in {"/api/profile", "/api/notes", "/api/forget-learning"} or (
                        path == "/api/settings" and "terms" in body)
                    current = server.app.data_root.name if server.app.data_root else None
                    if personal_write and body.get("personal_key") != current:
                        return self._send(409, {"message": "계정이 바뀌었습니다. 설정을 새로 불러온 뒤 다시 저장해 주세요."})
                    return self._dispatch_post(path, body)

            def _dispatch_post(self, path, body):
                if path == "/api/window":
                    action = {"welcome": server.open_welcome, "recovery": server.open_recovery}.get(body.get("name"))
                    if not action:
                        return self._send(400, {"error": "bad_request"})
                    action()
                    return self._send(200, {"ok": True})
                if path == "/api/welcome/complete":
                    try:
                        server.app.complete_welcome(bring_legacy=body.get("import_legacy") is True)
                    except (AccountError, OSError) as error:
                        return self._send(409, {"message": str(error)})
                    return self._send(200, {"ready": True})
                if path == "/api/recovery":
                    try:
                        server.app.recovery_action(body.get("id"), body.get("action"))
                    except (ValueError, OSError):
                        return self._send(409, {"error": "recovery_unavailable"})
                    return self._send(200, {"ok": True})
                if path in {"/api/profile", "/api/notes", "/api/forget-learning"} and not server.app.data_root:
                    return self._send(401, {"message": "계정을 먼저 연결해 주세요."})
                if path == "/api/forget-learning":
                    try:
                        server.app.forget_learning()
                    except (AccountError, OSError) as error:
                        return self._send(409, {"message": str(error)})
                    return self._send(200, server.app.public_settings())
                if path == "/api/settings":
                    return self._send(200, server.app.update_settings(body))
                if path == "/api/profile":
                    if body.get("action") == "rebuild":
                        server.app.profile.rebuild()
                    elif body.get("action") == "reset":
                        server.app.profile.reset()
                    return self._send(200, server.app.public_settings())
                if path in {"/api/account/login", "/api/account/logout"} and (server.app.active or server.app.profile.building):
                    return self._send(409, {"message": "진행 중인 받아쓰기가 끝나면 계정을 변경해 주세요."})
                if path == "/api/account/login":
                    if server.app.account.token:
                        try:
                            server.app.account.logout()
                        except AccountError as error:
                            return self._send(503, {"message": str(error)})
                    server.app.account_status()
                    url = server.app.account.begin(server.httpd.server_port)
                    try:
                        os.startfile(url)
                    except OSError:
                        server.app.account.pending = None
                        return self._send(503, {"error": "browser_unavailable"})
                    return self._send(200, {"state": "waiting"})
                if path == "/api/account/logout":
                    try:
                        server.app.account.logout()
                    except AccountError as error:
                        return self._send(503, {"error": error.code})
                    return self._send(200, server.app.account_status())
                if path == "/api/notes":
                    old, new = str(body.get("old", "")).strip(), str(body.get("new", "")).strip()
                    if body.get("action") == "add" and old and new and old != new:
                        server.app.notes.add(old, new)
                    elif body.get("action") == "delete":
                        server.app.notes.delete(old)
                    return self._send(200, server.app.notes.listing())
                self._send(404, {"error": "not found"})

        return Handler
