import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from thock.speech import transcribe


class SpeechEndpoints(unittest.IsolatedAsyncioTestCase):
    async def test_endpoints_are_metadata_and_partial_words_can_change(self):
        class Socket:
            async def send(self, value):
                pass
            async def close(self):
                pass
            async def __aiter__(self):
                for tokens in [
                    [("하나.", True), ("<end>", True), (" 임시", False)],
                    [(" 둘.", True), ("<end>", True)],
                    [("<fin>", True)],
                ]:
                    yield json.dumps({"tokens": [{"text": text, "is_final": final} for text, final in tokens]})
        async def audio():
            yield bytes(1600)
        seen, endpoints = [], []
        with patch("thock.speech.websockets.connect", new_callable=AsyncMock, return_value=Socket()):
            result = await transcribe(audio(), "test", lambda: {}, seen.append, endpoints.append)
        self.assertEqual(result, "하나. 둘.")
        self.assertEqual(endpoints, ["하나.", "하나. 둘."])
        self.assertEqual(seen, ["하나. 임시", "하나. 둘.", "하나. 둘."])
        await asyncio.sleep(0)
