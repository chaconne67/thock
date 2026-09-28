"""Settings window: local page shown in an Edge app window."""

import hmac
import http.server
import json
import os
import secrets
import subprocess
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import HOME, log
from .correction import check_keys
from .sound import SOUNDS


class SettingsServer:
    """Serves settings.html on 127.0.0.1 with a per-run secret, so no other page can change settings."""

    def __init__(self, app):
        self.app, self.token = app, secrets.token_urlsafe(24)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def open(self):
        log.info("settings window opened")
        url = f"http://127.0.0.1:{self.httpd.server_port}/?t={self.token}"
        edge = next((p for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
                                 Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe")
                     if p.exists()), None)
        if edge:
            # Own profile, no extensions: machine-wide extensions otherwise open their own tabs next to settings.
            subprocess.Popen([str(edge), f"--app={url}", "--window-size=540,860", f"--user-data-dir={HOME / 'edge'}",
                              "--no-first-run", "--no-default-browser-check", "--disable-extensions"])
        else:
            os.startfile(url)

    def _handler(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _allowed(self):
                token = parse_qs(urlparse(self.path).query).get("t", [""])[0] or self.headers.get("X-Token", "")
                return (self.headers.get("Host") == f"127.0.0.1:{server.httpd.server_port}"
                        and hmac.compare_digest(token, server.token))

            def _send(self, code, body, content_type="application/json; charset=utf-8"):
                data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if not self._allowed():
                    return self._send(403, {"error": "forbidden"})
                path = urlparse(self.path).path
                if path == "/":
                    page = (Path(__file__).parent / "settings.html").read_text(encoding="utf-8")
                    return self._send(200, page.replace("__TOKEN__", server.token).encode("utf-8"),
                                      "text/html; charset=utf-8")
                if path == "/api/settings":
                    return self._send(200, server.app.public_settings())
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
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                path, s = urlparse(self.path).path, server.app.settings
                if path == "/api/settings":
                    return self._send(200, server.app.update_settings(body))
                if path == "/api/profile":
                    if body.get("action") == "rebuild":
                        server.app.profile.rebuild()
                    elif body.get("action") == "reset":
                        server.app.profile.reset()
                    return self._send(200, server.app.public_settings())
                if path == "/api/chatgpt/login":
                    login = server.app.auth.start_login()
                    os.startfile(login["url"])  # the user's normal browser, where they are signed in to ChatGPT
                    return self._send(200, server.app.public_settings())
                if path == "/api/chatgpt/logout":
                    server.app.auth.logout()
                    return self._send(200, server.app.public_settings())
                if path == "/api/notes":
                    old, new = str(body.get("old", "")).strip(), str(body.get("new", "")).strip()
                    if body.get("action") == "add" and old and new and old != new:
                        server.app.notes.add(old, new)
                    elif body.get("action") == "delete":
                        server.app.notes.delete(old)
                    return self._send(200, server.app.notes.listing())
                if path == "/api/check":
                    keys = check_keys(body.get("soniox_api_key") or s["soniox_api_key"],
                                      body.get("openrouter_api_key") or s["openrouter_api_key"])
                    if body.get("polish_provider", s["polish_provider"]) == "chatgpt":
                        try:
                            polish_ok = bool(server.app.auth.access()[0])  # refreshes the sign-in if needed
                        except Exception:
                            polish_ok = False
                    else:
                        polish_ok = keys["openrouter"]
                    return self._send(200, {"soniox": keys["soniox"], "polish": polish_ok})
                self._send(404, {"error": "not found"})

        return Handler
