"""Verify the real Session + Soniox + correction + inline field using a synthetic WAV.

Run on Windows: uv run python tests/check_live_pipeline.py synthetic-16k.wav
--focused uses an already focused disposable test field containing "앞  뒤", caret after "앞 ".
Never modifies user settings/history, and never prints keys, profiles, or personal terms.
"""
import argparse
import asyncio
import json
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def verify(path, focused, report_path):
    from thock import app as app_module
    from thock.config import SAMPLE_RATE, load_settings
    from thock.editwatch import field_reader
    from thock.win32 import capture_target
    from thock import win32
    threading.Thread(target=win32.run_key_hook, args=(lambda: 0, lambda event: None), daemon=True).start()
    async with asyncio.timeout(3):
        while not win32._input_tracking:
            await asyncio.sleep(0.01)
    settings = load_settings()
    settings["learn"] = False
    state = app_module.App(settings)
    state.notice = ""
    if field_reader().snapshot() != ("앞 ", "", " 뒤"):
        raise RuntimeError("focus the disposable verification field at its marked insertion point")
    with wave.open(str(path)) as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError("expected 16 kHz mono 16-bit PCM")
        audio = source.readframes(source.getnframes())
    completions = []
    original_polish = state.polisher.polish
    def polish(text, app):
        result = original_polish(text, app)
        completions.append((time.perf_counter(), result))
        return result
    state.polisher.polish = polish
    class RecordedMicrophone:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
        def start(self):
            pass
        def stop(self):
            pass
        def close(self):
            pass
    output_blocks = {"nonzero_before_release": 0}
    original_output = app_module.sd.RawOutputStream
    class ObservedOutput:
        def __init__(self, **kwargs):
            self.stream = original_output(**kwargs)
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def write(self, data):
            self.stream.write(data)
            if state.recording and state.recording.released is None and any(data):
                output_blocks["nonzero_before_release"] += 1
    fields = []
    original_field = app_module.InlineField
    def field(target):
        result = original_field(target)
        fields.append(result)
        return result
    with tempfile.TemporaryDirectory(prefix="thock-session-check-") as temporary:
        with patch.object(app_module, "HOME", Path(temporary)), patch.object(
                app_module.sd, "RawInputStream", RecordedMicrophone), patch.object(
                    app_module.sd, "RawOutputStream", ObservedOutput), patch.object(app_module, "InlineField", field):
            session = app_module.Session(state, None)
            state.recording = session
            state.active.add(session)
            changes = []
            previous = ("앞 ", "", " 뒤")
            step = SAMPLE_RATE // 10 * 2
            for offset in range(0, len(audio), step):
                if session.task.done():
                    break
                chunk = audio[offset:offset + step]
                session._on_audio(chunk, len(chunk) // 2, None, None)
                await asyncio.sleep(0.1)
                current = field_reader().snapshot() if capture_target() == (fields[0].target if fields else session.target) else None
                if current != previous:
                    changes.append((time.perf_counter(), current))
                    previous = current
            session.stop()
            await asyncio.wait_for(session.task, 45)
            record = json.loads((Path(temporary) / "history.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            result = field_reader().snapshot() if capture_target() == (fields[0].target if fields else session.target) else None
            report = {
                "live_updates_before_release": sum(t < session.released for t, _ in changes),
                "corrections_before_release": sum(t < session.released for t, _ in completions),
                "correction_calls": len(completions),
                "sound_nonzero_blocks_before_release": output_blocks["nonzero_before_release"],
                "preserved_surrounding_text": bool(result and result[0].startswith("앞 ") and result[2] == " 뒤"),
                "final_text_delivered": bool(result and result == ("앞 " + record.get("text", ""), "", " 뒤")),
                "raw": record.get("raw"), "text": record.get("text"),
                "stt_seconds": record.get("stt_seconds"), "total_seconds": record.get("total_seconds"),
                "error": record.get("error"), "polish_error": record.get("polish_error"),
                "notice": state.notice, "field_failure": fields[0].failure if fields else None,
                "target_still_focused": capture_target() == (fields[0].target if fields else session.target),
            }
            if report_path:
                report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
            if (report["error"] or report["polish_error"] or not report["final_text_delivered"]
                    or not report["preserved_surrounding_text"] or not report["live_updates_before_release"]):
                raise RuntimeError("live session verification failed")
            if (settings["sound_recording"] or settings["sound_processing"]) and not output_blocks["nonzero_before_release"]:
                raise RuntimeError("live typing sound did not reach the output device")
    state.sounds.set_mode(None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    parser.add_argument("--focused", action="store_true")
    parser.add_argument("--hold", type=int, default=0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    fixture = None
    if not args.focused:
        from tests.test_inline_windows import WindowsInline
        fixture = WindowsInline
        fixture.setUpClass()
        print("Waiting for the disposable test editor to be activated.", flush=True)
        from thock.win32 import user32
        deadline = time.monotonic() + 90
        while user32.GetForegroundWindow() != fixture.window:
            if time.monotonic() > deadline:
                raise RuntimeError("test editor was not activated; no dictation was started")
            time.sleep(0.1)
        fixture().setUp()
    try:
        asyncio.run(verify(args.wav, args.focused, args.report))
        if args.hold:
            time.sleep(args.hold)
    finally:
        if fixture:
            fixture.tearDownClass()


if __name__ == "__main__":
    main()
