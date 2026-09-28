"""Speech recognition: stream microphone audio to Soniox and get the final text."""

import asyncio
import json

import websockets

from .config import SAMPLE_RATE, SONIOX_MODEL, SONIOX_URL


_background = set()  # keeps fire-and-forget tasks alive until they finish


async def transcribe(chunks, api_key, get_context, on_text=lambda text: None):
    """Stream PCM chunks to Soniox; after the source ends, finalize and return the final text.
    get_context is called after connecting, so fixes learned while connecting are already included.
    on_text gets the text heard so far, words still being revised included, each time Soniox sends more."""
    for attempt in range(3):  # audio keeps buffering in the queue while we retry
        try:
            ws = await websockets.connect(SONIOX_URL, max_size=None, open_timeout=5)
            break
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(0.3 * (attempt + 1))
    await ws.send(json.dumps({
        "api_key": api_key, "model": SONIOX_MODEL, "audio_format": "pcm_s16le",
        "sample_rate": SAMPLE_RATE, "num_channels": 1,
        "language_hints": ["ko", "en"], "context": get_context(),
    }))

    async def send_audio():
        async for chunk in chunks:
            await ws.send(chunk)
        await ws.send(bytes(SAMPLE_RATE // 5 * 2))  # 200 ms silence, as Soniox recommends before finalize
        await ws.send(json.dumps({"type": "finalize"}))

    sender = asyncio.create_task(send_audio())
    parts = []
    try:
        async for message in ws:
            data = json.loads(message)
            if data.get("error_code"):
                raise RuntimeError(f"Soniox {data['error_code']}: {data.get('error_message')}")
            tokens = data.get("tokens", [])
            final = [t["text"] for t in tokens if t.get("is_final")]
            done = "<fin>" in final
            parts += final[: final.index("<fin>")] if done else final
            on_text("".join(parts + [t["text"] for t in tokens if not t.get("is_final")]))
            if done:
                break
        else:
            raise RuntimeError("Soniox closed before finalizing")
    finally:
        sender.cancel()
        # The close handshake takes about a second; the text is ready now, so close in the background.
        closing = asyncio.create_task(ws.close())
        _background.add(closing)
        closing.add_done_callback(_background.discard)
    return "".join(parts).strip()
