"""One bounded microphone stream to Soniox, with a deadline after recording ends."""
import asyncio
import json
import re
import websockets
from .config import SAMPLE_RATE, SONIOX_MODEL, SONIOX_URL

_background = set()
# Soniox only hears the words; the correction model decides the punctuation from the context (주인님 결정
# 2026-10-01). Its sentence marks go, marks inside a word or number (3.5, thock.cloud) stay. Removing only
# marks followed by a space or the end keeps a finalized prefix a prefix of the later text.
SENTENCE_MARKS = re.compile(r"[.,?!]+(?=\s|$)")


def unpunctuated(text):
    return SENTENCE_MARKS.sub("", text)


async def transcribe(chunks, api_key, get_context, on_text=lambda text: None, on_endpoint=lambda text: None,
                     finalize_timeout=5):
    """on_text gets the text heard so far (words still being revised included); on_endpoint gets the
    finalized text each time Soniox detects the end of a spoken phrase."""
    for attempt in range(3):
        try:
            ws = await websockets.connect(SONIOX_URL, max_size=2 ** 20, open_timeout=5)
            break
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(0.3 * (attempt + 1))
    sender = receiver = None
    try:
        await ws.send(json.dumps({
            "api_key": api_key, "model": SONIOX_MODEL, "audio_format": "pcm_s16le",
            "sample_rate": SAMPLE_RATE, "num_channels": 1,
            "language_hints": ["ko", "en"], "context": get_context(),
            "enable_endpoint_detection": True,
        }))

        async def send_audio():
            async for chunk in chunks:
                await ws.send(chunk)
            await ws.send(bytes(SAMPLE_RATE // 5 * 2))
            await ws.send(json.dumps({"type": "finalize"}))

        async def receive_text():
            parts = []
            async for message in ws:
                data = json.loads(message)
                if data.get("error_code"):
                    raise RuntimeError("speech service rejected the stream")
                tokens = data.get("tokens", [])
                done, endpoints = False, []
                for token in tokens:
                    if not token.get("is_final"):
                        continue
                    if token["text"] == "<fin>":
                        done = True
                        break
                    if token["text"] == "<end>":
                        endpoints.append(unpunctuated("".join(parts)))
                    else:
                        parts.append(token["text"])
                on_text(unpunctuated("".join(parts + [t["text"] for t in tokens
                                                      if not t.get("is_final") and t["text"] not in ("<end>", "<fin>")])))
                for endpoint in endpoints:
                    on_endpoint(endpoint)
                if done:
                    return unpunctuated("".join(parts)).strip()
            raise RuntimeError("speech service closed before finalizing")

        sender, receiver = asyncio.create_task(send_audio()), asyncio.create_task(receive_text())
        completed, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
        if sender in completed:
            await sender  # Propagate a send failure instead of waiting forever for the receiver.
            return await asyncio.wait_for(receiver, timeout=finalize_timeout)
        return await receiver
    finally:
        for task in (sender, receiver):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (sender, receiver) if task), return_exceptions=True)
        async def close():
            try:
                await asyncio.wait_for(ws.close(), timeout=2)
            except (OSError, TimeoutError):
                pass
        closing = asyncio.create_task(close())
        _background.add(closing)
        closing.add_done_callback(_background.discard)
