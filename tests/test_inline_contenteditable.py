"""Live dictation and correction inside Chromium rich editors, like the Codex and Claude apps."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import urllib.request


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("CHROMEWEBDRIVER"), "Windows Chromium")
class ContentEditableInline(unittest.TestCase):
    def test_live_correction_replaces_its_tail(self):
        import thock.win32 as win32
        from thock.editwatch import field_reader
        executable = Path(os.environ["CHROMEWEBDRIVER"]) / "chromedriver.exe"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        driver = subprocess.Popen([str(executable), f"--port={port}", "--silent"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = f"http://127.0.0.1:{port}"

        def request(method, route, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url + route, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as result:
                return json.load(result)["value"]

        session = None
        reader = field_reader()
        try:
            for _ in range(100):
                try:
                    request("GET", "/status")
                    break
                except OSError:
                    time.sleep(0.1)
            session = request("POST", "/session", {"capabilities": {"alwaysMatch": {
                "browserName": "chrome", "goog:chromeOptions": {"args": [
                    "--force-renderer-accessibility", "--no-first-run", "--window-size=800,600"]}}}})["sessionId"]
            route = f"/session/{session}"
            request("POST", route + "/url", {"url": "about:blank"})

            def script(source, *args):
                return request("POST", route + "/execute/sync", {"script": source, "args": list(args)})

            steps = ("이게 말소리와", "이게 말소리와 함께 타이핑 소리도 끝나야",
                     "이게 말소리와 함께 타이핑 소리도 끝나야 한다.", "이게 말소리와 함께 타이핑도 끝나야 한다.")
            for kind in ("pre-wrap-paragraph", "plain", "textarea"):
                with self.subTest(kind=kind):
                    script("""
                        const kind = arguments[0];
                        document.body.innerHTML = kind === 'textarea' ? '<textarea></textarea>'
                            : kind === 'plain' ? '<div contenteditable style="height:200px"></div>'
                            : '<div contenteditable style="white-space:pre-wrap;height:200px"><p><br></p></div>';
                        const field = document.body.firstElementChild;
                        field.focus();
                        if (kind === 'pre-wrap-paragraph') {
                            const range = document.createRange();
                            range.setStart(field.firstChild, 0); range.collapse(true);
                            getSelection().removeAllRanges(); getSelection().addRange(range);
                        }
                    """, kind)
                    time.sleep(0.3)
                    with patch("thock.win32._input_tracking", True):
                        target = win32.capture_target()
                        initial = reader.snapshot()
                        field = win32.InlineField(target)
                        results = []
                        for text in steps:
                            results.append((text, field.update(text), field.failure))
                            time.sleep(0.3)
                    dom = script("const f=document.body.firstElementChild; return f.value ?? f.innerText;")
                    report = json.dumps({"kind": kind, "initial": initial, "results": results, "dom": dom},
                                        ensure_ascii=True)
                    self.assertTrue(all(ok for _, ok, _ in results), report)
                    self.assertEqual(dom.strip(), steps[-1], report)
        finally:
            if session:
                request("DELETE", f"/session/{session}")
            driver.terminate()
            driver.wait(timeout=10)
