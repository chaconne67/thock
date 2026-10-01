"""One dictation's live draft, corrected as a whole when the speaker pauses and at the end.

The writer owns only its inserted text. Speech callbacks never wait for UI or a model.
The correction model always sees everything said so far, so punctuation follows the whole text
rather than each short phrase, and a mark set earlier can still move.
When the user edits during dictation, what was written stays as is and later speech starts
again at their caret.
"""
import asyncio

PAUSE = 1.0  # seconds without new words after a phrase ends before the whole text is corrected


class LiveDictation:
    def __init__(self, write, polish, apply_notes, on_error, restart=lambda: False, mark=lambda name, **values: None):
        self.write, self.polish, self.apply_notes, self.on_error = write, polish, apply_notes, on_error
        self.restart, self.mark = restart, mark
        self.piece = 0  # bumped when later speech moves to the user's new caret
        self.start = ""  # finalized speech before this piece; the rest is corrected as one text
        self.heard = self.boundary = self.processed = self.corrected = ""
        self.changed, self.segment_ready, self.spoke = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.ending = False
        self.blocked = False
        self.error = None
        self.delivered = ""
        self.writer = asyncio.create_task(self._write())
        self.corrector = asyncio.create_task(self._correct())

    def update(self, text):
        if text.strip() != self.heard:
            self.spoke.set()
        self.heard = text.strip()
        self.changed.set()

    def endpoint(self, text):
        self.boundary = text.strip()
        self.spoke.clear()
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
            if not self.ending:
                try:
                    await asyncio.wait_for(self.spoke.wait(), PAUSE)
                    continue  # still speaking: the next pause corrects all of it
                except TimeoutError:
                    pass
            boundary, piece = self.boundary, self.piece
            if not boundary.startswith(self.processed):
                raise RuntimeError("speech endpoint moved backwards")
            whole = boundary[len(self.start):]
            self.mark("endpoint", length=len(whole))
            if boundary != self.processed and whole.strip():
                content = corrected = whole.strip()
                if self.polish:
                    started = asyncio.get_running_loop().time()
                    try:
                        corrected = await asyncio.to_thread(self.polish, content)
                    except Exception as error:
                        corrected = None
                        self.error = type(error).__name__
                        self.on_error("문장을 다듬지 못해 인식한 원문을 남겼습니다.")
                    # A rejected correction (None) leaves the earlier correction in place; newer words stay as heard.
                    self.mark("polish", length=len(content), out=len(corrected) if corrected is not None else None,
                              ms=round((asyncio.get_running_loop().time() - started) * 1000), error=self.error)
                if corrected is not None and piece == self.piece:  # text the user took over is not corrected
                    leading = whole[:len(whole) - len(whole.lstrip())]
                    trailing = whole[len(whole.rstrip()):]
                    self.corrected = leading + self.apply_notes(corrected) + trailing
                    self.processed = boundary
                    self.changed.set()
            if self.ending and boundary == self.boundary:
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
                    if not result:
                        self.mark("restart" if restarted else "blocked")
                    if restarted:
                        if self.delivered:
                            # The user edited: keep what is written, continue after the last finished phrase.
                            self.piece += 1
                            self.start = self.processed = self.boundary
                            self.corrected, self.delivered = "", ""
                        self.changed.set()
                    elif not result:
                        self.blocked = True
                        self.on_error("입력 위치나 글이 바뀌어 자동 입력을 멈췄습니다.")
                    else:
                        self.delivered = text
            if self.ending and self.corrector.done() and not self.changed.is_set():
                return

    async def finish(self, raw):
        self.update(raw)
        self.endpoint(raw)
        self.ending = True
        self.spoke.set()  # a correction waiting for a pause runs now
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
