import asyncio
import threading
import unittest
from thock.live_input import LiveDictation


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


class LiveInput(unittest.IsolatedAsyncioTestCase):
    def make(self, polish=None, write=None):
        self.writes, self.errors = [], []
        def record(text):
            self.writes.append(text)
            return True
        live = LiveDictation(write or record, polish, lambda text: text, self.errors.append)
        self.addAsyncCleanup(live.close)
        return live

    async def test_draft_is_delivered_before_finish_and_revised_in_place(self):
        live = self.make(lambda text: text.replace("회의 하자", "회의하자."))
        live.update("내일")
        await until(lambda: self.writes)
        live.update("내일 회의 하자")
        await until(lambda: self.writes[-1] == "내일 회의 하자")
        self.assertEqual(await live.finish("내일 회의 하자"), "내일 회의하자.")
        self.assertEqual(self.writes[-1], "내일 회의하자.")

    async def test_words_go_in_as_heard_and_the_whole_is_corrected_once_at_the_end(self):
        # 주인님 결정 2026-10-03: no correction at pauses, one of the whole text when the dictation ends.
        calls = []
        def polish(text):
            calls.append(text)
            return text + "."
        live = self.make(polish)
        live.update("첫 문장")
        live.endpoint("첫 문장")
        live.update("첫 문장 다음 문장")
        live.endpoint("첫 문장 다음 문장")
        await until(lambda: self.writes and self.writes[-1] == "첫 문장 다음 문장")
        await asyncio.sleep(0.3)
        self.assertEqual(calls, [])  # nothing corrected while speaking, however long the pause
        self.assertEqual(await live.finish("첫 문장 다음 문장"), "첫 문장 다음 문장.")
        self.assertEqual((calls, self.writes[-1]), (["첫 문장 다음 문장"], "첫 문장 다음 문장."))

    async def test_endpoint_spaces_and_empty_finalization_do_not_duplicate_text(self):
        calls = []
        def polish(text):
            calls.append(text)
            return text
        live = self.make(polish)
        live.update("하나. ")
        live.endpoint("하나. ")
        live.update("하나. 둘.")
        live.endpoint("하나. 둘.")
        self.assertEqual(await live.finish("하나. 둘."), "하나. 둘.")
        self.assertEqual(calls, ["하나. 둘."])  # the whole text, once

    async def test_a_field_that_cannot_be_written_holds_everything_until_a_new_dictation_lets_it_go(self):
        self.errors = []
        calls, paused = [], []
        def moved(text):
            calls.append(text)
            return False
        live = LiveDictation(moved, None, lambda text: text, self.errors.append, on_pause=lambda: paused.append(1))
        self.addAsyncCleanup(live.close)
        live.update("초안")
        await until(lambda: live.paused)
        live.update("새 초안")  # still heard while the field is away
        finishing = asyncio.create_task(live.finish("새 초안"))
        await asyncio.sleep(0.4)
        self.assertFalse(finishing.done())  # held, waiting for the field
        live.release()
        self.assertEqual(await finishing, "새 초안")
        self.assertEqual((calls, paused, live.complete, self.errors), (["초안"], [1], False, []))

    async def test_writing_goes_on_when_the_field_is_back(self):
        self.errors = []
        fields, back = [[]], []
        def write(text):
            if not back:
                return False  # another window took the front
            fields[-1].append(text)
            return True
        def resume():
            return back[0] if back else None
        resumed = []
        live = LiveDictation(write, None, lambda text: text, self.errors.append, resume=resume,
                             on_resume=lambda: resumed.append(1))
        self.addAsyncCleanup(live.close)
        live.update("앞 말")
        await until(lambda: live.paused)
        live.update("앞 말 뒤 말")
        back.append("same")
        self.assertEqual(await live.finish("앞 말 뒤 말"), "앞 말 뒤 말")
        self.assertEqual((fields, live.complete, resumed), ([["앞 말 뒤 말"]], True, [1]))  # the message goes

    async def test_a_field_the_user_changed_meanwhile_gets_only_the_rest_at_their_caret(self):
        self.errors = []
        writes, state = [], {"ok": True, "back": None}
        def write(text):
            writes.append(text)
            return state["ok"]
        live = LiveDictation(write, None, lambda text: text, self.errors.append, resume=lambda: state["back"])
        self.addAsyncCleanup(live.close)
        live.update("앞 말")
        await until(lambda: writes == ["앞 말"])
        state["ok"] = False
        live.update("앞 말 뒤 말")
        await until(lambda: live.paused)
        state["ok"], state["back"] = True, "moved"
        self.assertEqual(await live.finish("앞 말 뒤 말"), "앞 말 뒤 말")
        self.assertEqual((writes[-1], live.complete), (" 뒤 말", True))

    async def test_after_a_user_edit_later_speech_starts_at_the_new_caret(self):
        fields = [[]]
        def write(text):
            if text.endswith("다시 말한다"):
                if len(fields) == 1:
                    return False  # the user edited the field since the last write
            fields[-1].append(text)
            return True
        def restart():
            fields.append([])
            return True
        def polish(text):
            return text.replace("말한다", "말한다.")
        self.errors = []
        live = LiveDictation(write, polish, lambda text: text, self.errors.append, restart)
        self.addAsyncCleanup(live.close)
        live.update("지울 문장.")
        live.endpoint("지울 문장.")
        await until(lambda: fields[0])
        live.update("지울 문장. 다시 말한다")
        await until(lambda: len(fields) == 2 and fields[1])
        self.assertEqual(await live.finish("지울 문장. 다시 말한다"), "다시 말한다.")
        self.assertEqual(fields[0], ["지울 문장."])
        self.assertEqual(fields[1][0], "다시 말한다")
        self.assertEqual(fields[1][-1], "다시 말한다.")
        self.assertFalse(live.blocked)
        self.assertEqual(self.errors, [])

    async def test_polish_failure_preserves_raw_and_reports_failure(self):
        def fail(text):
            raise TimeoutError()
        live = self.make(fail)
        self.assertEqual(await live.finish("남길 글"), "남길 글")
        self.assertEqual(self.writes[-1], "남길 글")
        self.assertEqual(live.error, "TimeoutError")
        self.assertEqual(len(self.errors), 1)

    async def test_cancelling_waits_for_an_inflight_write(self):
        started, release = threading.Event(), threading.Event()
        def slow(text):
            started.set()
            release.wait(2)
            self.writes.append(text)
            return True
        live = self.make(write=slow)
        live.update("입력 중")
        await until(started.is_set)
        closing = asyncio.create_task(live.close())
        await asyncio.sleep(0.01)
        self.assertFalse(closing.done())
        release.set()
        await closing
        self.assertEqual(self.writes, ["입력 중"])
        live.update("늦게 도착한 글")
        await asyncio.sleep(0.01)
        self.assertEqual(self.writes, ["입력 중"])

    async def test_empty_audio_does_not_modify_selection(self):
        live = self.make()
        self.assertEqual(await live.finish(""), "")
        self.assertEqual(self.writes, [])

    async def test_partial_revision_only_changes_unconfirmed_text(self):
        live = self.make(lambda text: text.upper())
        live.update("hello. old")
        live.endpoint("hello.")
        live.update("hello. new")
        self.assertEqual(await live.finish("hello. new"), "HELLO. NEW")
