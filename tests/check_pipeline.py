"""Feed WAV files (16 kHz mono 16-bit) through the real transcribe() and Polisher at real-time pace.

Usage: uv run python tests/check_pipeline.py file1.wav [file2.wav ...]
Prints recognized text, corrected text, and seconds from "key release" (end of audio) to each result.
"""

import asyncio
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from thock.config import HOME, SAMPLE_RATE, load_settings  # noqa: E402
from thock.correction import ChatGPTAuth, Polisher  # noqa: E402
from thock.learning import Profile, TypoNotes  # noqa: E402
from thock.speech import transcribe  # noqa: E402


async def realtime_chunks(path, marks):
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SAMPLE_RATE, 1, 2), path
        step = SAMPLE_RATE // 10
        while frames := w.readframes(step):
            yield frames
            await asyncio.sleep(0.1)
    marks["released"] = time.perf_counter()


async def main(paths):
    s = load_settings()
    notes = TypoNotes(HOME / "typo_notes.json")
    profile = Profile(HOME / "profile.json", HOME / "history.jsonl")
    polisher = Polisher(s, notes, ChatGPTAuth(HOME / "chatgpt_auth.json"), profile)
    for path in paths:
        marks = {}
        raw = await transcribe(realtime_chunks(path, marks), s["soniox_api_key"],
                               lambda: profile.context("WindowsTerminal.exe", s["terms"] + notes.terms()))
        stt = time.perf_counter() - marks["released"]
        text = await asyncio.to_thread(polisher.polish, raw, "WindowsTerminal.exe") if raw else raw
        total = time.perf_counter() - marks["released"]
        print(f"{Path(path).name}: stt {stt:.2f}s, total {total:.2f}s\n  raw : {raw}\n  text: {text}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
