"""Feed WAV files (16 kHz mono 16-bit) through the real transcribe() and Polisher at real-time pace.

Usage: uv run python tests/check_pipeline.py file1.wav [file2.wav ...]
Prints recognized text, corrected text, and seconds from "key release" (end of audio) to each result,
and how soon and how often the live preview got text while the audio was still playing.
"""

import asyncio
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
    notes = TypoNotes(HOME / "typo_notes.json")
    profile = Profile(HOME / "profile.json", HOME / "history.jsonl")
    account = Account()
    if not account.token:
        raise SystemExit("Thock에서 AI Shift에 먼저 로그인해 주세요.")
    polisher = Polisher(s, notes, account, profile)
    for path in paths:
        marks, heard = {}, []
        temporary_key = await asyncio.to_thread(account.session_key)
        raw = await transcribe(realtime_chunks(path, marks), temporary_key,
                               lambda: profile.context("WindowsTerminal.exe", s["terms"] + notes.terms()),
                               lambda text: heard.append((time.perf_counter(), text.strip())))
        live = [(t, text) for t, text in heard if text and t < marks["released"]]
        stt = time.perf_counter() - marks["released"]
        text = await asyncio.to_thread(polisher.polish, raw, "WindowsTerminal.exe") if raw else raw
        total = time.perf_counter() - marks["released"]
        print(f"{Path(path).name}: stt {stt:.2f}s, total {total:.2f}s\n  raw : {raw}\n  text: {text}")
        if live:
            print(f"  live: first text {live[0][0] - marks['started']:.2f}s after start, {len(live)} updates"
                  f" before release, halfway: {live[len(live) // 2][1]}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
