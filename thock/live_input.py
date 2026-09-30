"""One dictation's live draft and ordered, endpoint-based corrections.

The writer owns only its inserted text. Speech callbacks never wait for UI or a model.
When the user edits during dictation, what was written stays as is and later speech starts
again at their caret.
"""
import asyncio


class LiveDictation:
    def __init__(self, write, polish, apply_notes, on_error, restart=lambda: False):
        self.write, self.polish, self.apply_notes, self.on_error = write, polish, apply_notes, on_error
        self.restart = restart
        self.waiting = False  # no text field has the caret yet; words are kept until one does
        self.piece = 0  # bumped when later speech moves to the user's new caret
        self.heard = self.boundary = self.processed = self.corrected = ""
        self.changed, self.segment_ready = asyncio.Event(), asyncio.Event()
        self.ending = False
        self.blocked = False
        self.error = None
        self.delivered = ""
        self.writer = asyncio.create_task(self._write())
        self.corrector = asyncio.create_task(self._correct())

    def update(self, text):
        self.heard = text.strip()
        self.changed.set()

    def endpoint(self, text):
        self.boundary = text.strip()
        self.segment_ready.set()

    def text(self):
        # Soniox final tokens are append-only; partial tokens may be replaced.
        if not self.heard.startswith(self.processed):
            raise RuntimeError("finalized speech changed")
        return (self.corrected + self.apply_notes(self.heard[len(self.processed):])).lstrip()

    async def _correct(self):
        while True:
            await self.segment_ready.wait()
            self.segment_ready.clear()
            boundary = self.boundary
            if not boundary.startswith(self.processed):
                raise RuntimeError("speech endpoint moved backwards")
            segment = boundary[len(self.processed):]
            piece = self.piece
            if segment:
                content = segment.strip()
                corrected = content
                if content and self.polish:
                    try:
                        corrected = await asyncio.to_thread(self.polish, content)
                    except Exception as error:
                        self.error = type(error).__name__
                        self.on_error("문장을 다듬지 못해 인식한 원문을 남겼습니다.")
                if piece == self.piece:  # a correction for text the user already took over is dropped
                    leading = segment[:len(segment) - len(segment.lstrip())]
                    trailing = segment[len(segment.rstrip()):]
                    self.corrected += leading + self.apply_notes(corrected) + trailing
                    self.processed = boundary
                    self.changed.set()
            if self.ending and self.processed == self.boundary:
                return

    async def _write(self):
        while True:
            await self.changed.wait()
            self.changed.clear()
            if not self.blocked:
                text = self.text()
                if text != self.delivered:
                    try:
                        result = await asyncio.to_thread(self.write, text)
                    except Exception:
                        result = False
                    restarted = (await asyncio.to_thread(self.restart)) if not result else False
                    if restarted:
                        if self.delivered:
                            # The user edited: keep what is written, continue after the last finished phrase.
                            self.piece += 1
                            self.processed, self.corrected, self.delivered = self.boundary, "", ""
                        self.waiting = False
                        self.changed.set()
                    elif not result and restarted is None:
                        self.waiting = True
                    elif not result:
                        self.blocked = True
                        self.on_error("입력 위치나 글이 바뀌어 자동 입력을 멈췄습니다.")
                    else:
                        self.delivered, self.waiting = text, False
            if self.ending and self.corrector.done() and not self.changed.is_set():
                return

    async def finish(self, raw):
        self.update(raw)
        self.endpoint(raw)
        self.ending = True
        await self.corrector
        self.changed.set()
        await self.writer
        return self.text()

    async def close(self):
        # Let an already dispatched UI write finish; cancelling to_thread cannot stop it.
        self.blocked = True
        self.ending = True
        if not self.corrector.done():
            self.corrector.cancel()
        await asyncio.gather(self.corrector, return_exceptions=True)
        self.changed.set()
        await self.writer
