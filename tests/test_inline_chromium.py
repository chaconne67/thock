"""Windows Chromium text fields exercised through Thock's real input path."""
import json
from itertools import product
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
import urllib.request


@unittest.skipUnless(sys.platform == "win32", "Windows browser input")
class ChromiumInline(unittest.TestCase):
    def test_native_input_across_web_field_types_and_rebuilt_controls(self):
        from thock.editwatch import field_reader, _snapshot
        from thock.win32 import InlineField, capture_target
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
            kinds = ("input", "textarea", "contenteditable", "rebuilt-textarea")
            for kind, initial in product(kinds, ("", "앞  뒤")):
                with self.subTest(kind=kind, initial=initial):
                    before, after = ("앞 ", " 뒤") if initial else ("", "")
                    script("""
                        const kind = arguments[0], initial = arguments[1], offset = initial ? 2 : 0;
                        document.body.innerHTML = kind === 'contenteditable'
                            ? '<div contenteditable style="height:200px;white-space:pre-wrap"></div>'
                            : kind === 'input' ? '<input>' : '<textarea></textarea>';
                        let field = document.body.firstElementChild;
                        const reset = () => {
                            if (kind === 'contenteditable') {
                                field.textContent = initial; field.focus();
                                const range = document.createRange();
                                range.selectNodeContents(field);
                                if (initial) range.setStart(field.firstChild, offset);
                                range.collapse(true);
                                getSelection().removeAllRanges(); getSelection().addRange(range);
                                field.focus();
                            } else { field.value = initial; field.focus(); field.setSelectionRange(offset, offset); }
                        };
                        if (kind === 'rebuilt-textarea') field.addEventListener('input', function rebuild(e) {
                            const old = e.target, start = old.selectionStart, end = old.selectionEnd;
                            field = old.cloneNode(); field.value = old.value; old.replaceWith(field);
                            field.focus(); field.setSelectionRange(start, end);
                            field.addEventListener('input', rebuild);
                        });
                        window.eventsAbort?.abort(); window.eventsAbort = new AbortController();
                        window.events = [];
                        for (const name of ['keydown', 'keyup', 'paste', 'input'])
                            document.body.addEventListener(name, e => window.events.push([name, e.key]), {capture:true, signal: window.eventsAbort.signal});
                        window.resetCaret = reset; reset();
                    """, kind, initial)
                    element = request('POST', route + '/element', {'using':'css selector', 'value':'body > :first-child'})
                    element_id = element['element-6066-11e4-a52e-4f735466cecf']
                    request('POST', route + f'/element/{element_id}/click', {})
                    script('window.resetCaret();')
                    time.sleep(0.2)
                    target = capture_target()
                    reader = field_reader()
                    try:
                        with reader.selection() as (document, selected):
                            raw = _snapshot(document, selected)
                    except Exception as error:
                        raw = repr(error)
                    print('initial', kind, ascii(initial), 'snapshot', ascii(reader.snapshot()),
                          'value', ascii(reader.read_focused()), 'raw', ascii(raw), flush=True)
                    self.assertEqual(reader.snapshot(), (before, "", after), (kind, target, reader.snapshot()))
                    field = InlineField(target)
                    for text in ("소", "소리가 잘 들려", "소리가 잘 들려요.", "소리가 들립니다."):
                        delivered = field.update(text)
                        actual = script("const f=document.body.firstElementChild; return f.value ?? f.innerText;")
                        self.assertTrue(delivered, (kind, field.failure, target, capture_target(), reader.snapshot(), actual, script("return window.events;")))
                        self.assertEqual(actual, before + text + after)
        finally:
            if session:
                request("DELETE", f"/session/{session}")
            driver.terminate()
            driver.wait(timeout=10)
