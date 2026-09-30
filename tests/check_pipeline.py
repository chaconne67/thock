"""Feed WAV files (16 kHz mono 16-bit) through the real transcribe() and Polisher at real-time pace.

Usage: uv run python tests/check_pipeline.py file1.wav [file2.wav ...]
Prints recognized text, corrected text, and seconds from "key release" (end of audio) to each result,
and how soon and how often the live preview got text while the audio was still playing.
"""

import asyncio
import hashlib
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from thock.config import HOME, SAMPLE_RATE, load_settings  # noqa: E402
from thock.account import Account  # noqa: E402
from thock.correction import Polisher  # noqa: E402
from thock.learning import Profile, TypoNotes  # noqa: E402
from thock.speech import transcribe  # noqa: E402


async def realtime_chunks(path, marks):
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SAMPLE_RATE, 1, 2), path
        step = SAMPLE_RATE // 10
        marks["started"] = time.perf_counter()
        while frames := w.readframes(step):
            yield frames
            await asyncio.sleep(0.1)
    marks["released"] = time.perf_counter()


async def main(paths):
    s = load_settings()
    account = Account()
    status = account.status(force=True)
    if not status.get("ready"):
        raise SystemExit(status.get("error") or "Thock에서 AI Shift 계정과 이용권을 확인해 주세요.")
    root = HOME / "accounts" / hashlib.sha256(str(status["account_id"]).encode()).hexdigest()[:24]
    from thock.personal import read_data
    s["terms"] = read_data(root / "terms.protected", [])
    notes = TypoNotes(root / "notes.protected")
    profile = Profile(root / "profile.protected", root / "history.protected")
    polisher = Polisher(s, notes, account, profile)
    for path in paths:
        marks, heard = {}, []
        with wave.open(str(path)) as source:
            recorded_ms = round(source.getnframes() * 1000 / source.getframerate())
        if recorded_ms > 120000:
            raise SystemExit("검증 음성은 120초 이하여야 합니다.")
        grant = await asyncio.to_thread(account.start_session)
        if recorded_ms > grant["max_session_seconds"] * 1000:
            raise SystemExit("이용권의 남은 시간이 검증 음성보다 짧습니다.")
        raw = await transcribe(realtime_chunks(path, marks), grant["api_key"],
                               lambda: profile.context("WindowsTerminal.exe", s["terms"] + notes.terms()),
                               lambda text: heard.append((time.perf_counter(), text.strip())))
        live = [(t, text) for t, text in heard if text and t < marks["released"]]
        stt = time.perf_counter() - marks["released"]
        text = await asyncio.to_thread(polisher.polish, raw, "WindowsTerminal.exe") if raw else raw
        total = time.perf_counter() - marks["released"]
        await asyncio.to_thread(account.report, grant["session_id"], recorded_ms, "delivered",
                                input_mode="hold", stt_ms=round(stt*1000), total_ms=round(total*1000))
        print(f"{Path(path).name}: stt {stt:.2f}s, total {total:.2f}s\n  raw : {raw}\n  text: {text}")
        if live:
            print(f"  live: first text {live[0][0] - marks['started']:.2f}s after start, {len(live)} updates"
                  f" before release, halfway: {live[len(live) // 2][1]}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
