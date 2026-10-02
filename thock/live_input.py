"""One dictation's live draft, corrected as a whole when the speaker pauses and at the end.

The writer owns only its inserted text. Speech callbacks never wait for UI or a model.
The correction model always sees everything said so far, so punctuation follows the whole text
rather than each short phrase, and a mark set earlier can still move.
When the user edits during dictation, what was written stays as is and later speech starts
again at their caret.
Hearing never depends on writing (주인님 2026-10-02): when the field cannot be written (another window in
front, the field changed, anything), writing pauses while everything heard is kept, and goes on when the
dictation's field is back, until a new dictation lets the held text go.
"""
import asyncio

PAUSE = 1.0  # seconds without new words after a phrase ends before the whole text is corrected


class LiveDictation:
    def __init__(self, write, polish, apply_notes, on_error, restart=lambda: False, mark=lambda name, **values: None,
                 resume=lambda: None, on_pause=lambda: None):
        self.write, self.polish, self.apply_notes, self.on_error = write, polish, apply_notes, on_error
        self.restart, self.mark, self.resume, self.on_pause = restart, mark, resume, on_pause
        self.paused = self.released = False
        self.skip = 0  # leading letters already in the field before the user moved the caret during a pause
        self.written = ""  # the whole text as of the last write the field showed
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
                    # A failed correction (None) leaves the earlier correction in place; newer words stay as heard.
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

    @property
    def complete(self):
        """Everything heard is in the field."""
        return not self.blocked and not self.paused and self.text()[self.skip:] == self.delivered

    def release(self):
        """A new dictation lets a held text go."""
        self.released = True

    async def _wait_for_field(self):
        while self.paused and not self.blocked:
            if self.released:
                self.blocked, self.paused = True, False
                self.mark("released")
                return
            back = await asyncio.to_thread(self.resume)
            if back:
                if back == "moved":  # the user changed the field: what is there stays, the rest goes at the caret
                    full = self.text()
                    common = next((i for i, (a, b) in enumerate(zip(self.written, full)) if a != b),
                                  min(len(self.written), len(full)))
                    self.skip, self.delivered = common, ""
                self.paused = False
                self.mark("resumed", how=back)
                return
            await asyncio.sleep(0.3)

    async def _write(self):
        while True:
            if self.paused:
                await self._wait_for_field()
            else:
                await self.changed.wait()
            self.changed.clear()
            if not self.blocked and not self.paused:
                full = self.text()
                text = full[self.skip:]
                if text != self.delivered:
                    try:
                        result = await asyncio.to_thread(self.write, text)
                    except Exception:
                        result = False
                    restarted = (await asyncio.to_thread(self.restart)) if not result else False
                    if not result:
                        self.mark("restart" if restarted else "paused")
                    if restarted:
                        if self.delivered:
                            # The user edited: keep what is written, continue after the last finished phrase.
                            self.piece += 1
                            self.start = self.processed = self.boundary
                            self.corrected, self.delivered, self.skip, self.written = "", "", 0, ""
                        self.changed.set()
                    elif not result:
                        self.paused = True
                        self.on_pause()
                    else:
                        self.delivered, self.written = text, full
            if self.ending and self.corrector.done() and not self.changed.is_set() and not self.paused:
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
