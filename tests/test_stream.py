import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from thock.speech import transcribe

class Socket:
    def __init__(self, messages=None, fail_send=False):
        self.messages = messages or []
        self.sent = []
        self.fail_send = fail_send
        self.closed = False
        self.finalized = asyncio.Event()

    async def send(self, value):
        if self.fail_send and isinstance(value, bytes):
            raise OSError("send failed")
        self.sent.append(value)
        if isinstance(value, str) and json.loads(value).get("type") == "finalize":
            self.finalized.set()

    def __aiter__(self):
        return self.receive()

    async def receive(self):
        await self.finalized.wait()
        for value in self.messages:
            yield json.dumps(value)
        if not self.messages:
            await asyncio.Event().wait()

    async def close(self):
        self.closed = True

async def chunks():
    yield bytes(1600)

class StreamContract(unittest.IsolatedAsyncioTestCase):
    async def test_final_text_and_known_finalization_silence(self):
        ws = Socket([{"tokens":[{"text":"안녕하세요.", "is_final":True},{"text":"<fin>", "is_final":True}]}])
        with patch("thock.speech.websockets.connect", new_callable=AsyncMock, return_value=ws):
            text = await transcribe(chunks(), "test", lambda:{})
        self.assertEqual(text, "안녕하세요")  # Soniox hears the words; punctuation is the correction model's
        self.assertEqual(len(ws.sent[-2]), 6400)
        await asyncio.sleep(0.01)
        self.assertTrue(ws.closed)

    async def test_no_final_response_has_bounded_deadline(self):
        ws = Socket()
        with patch("thock.speech.websockets.connect", new_callable=AsyncMock, return_value=ws):
            with self.assertRaises(TimeoutError):
                await transcribe(chunks(), "test", lambda:{}, finalize_timeout=0.02)

    async def test_sender_failure_is_not_hidden_by_waiting_receiver(self):
        ws = Socket(fail_send=True)
        with patch("thock.speech.websockets.connect", new_callable=AsyncMock, return_value=ws):
            with self.assertRaises(OSError):
                await asyncio.wait_for(transcribe(chunks(), "test", lambda:{}), timeout=1)
