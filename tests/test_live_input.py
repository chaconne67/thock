import asyncio
import threading
import unittest
from unittest.mock import patch
from thock.live_input import LiveDictation


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


class LiveInput(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        pause = patch("thock.live_input.PAUSE", 0.05)
        pause.start()
        self.addCleanup(pause.stop)

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

    async def test_slow_correction_keeps_new_words_and_does_not_block_live_input(self):
        started, release = threading.Event(), threading.Event()
        def polish(text):
            if text == "첫 문장.":
                started.set()
                release.wait(2)
            return text.replace("첫", "교정한 첫")
        live = self.make(polish)
        live.update("첫 문장.")
        live.endpoint("첫 문장.")
        await until(started.is_set)
        live.update("첫 문장. 다음 문장")
        await until(lambda: self.writes and self.writes[-1].endswith("다음 문장"))
        release.set()
        await until(lambda: live.processed == "첫 문장.")
        self.assertEqual(await live.finish("첫 문장. 다음 문장"), "교정한 첫 문장. 다음 문장")
        self.assertEqual(self.writes[-1], "교정한 첫 문장. 다음 문장")

    async def test_endpoint_spaces_and_empty_finalization_do_not_duplicate_text(self):
        calls = []
        def polish(text):
            calls.append(text)
            return text
        live = self.make(polish)
        live.update("하나. ")
        live.endpoint("하나. ")
        await until(lambda: live.processed == "하나.")
        live.update("하나. 둘.")
        live.endpoint("하나. 둘.")
        self.assertEqual(await live.finish("하나. 둘."), "하나. 둘.")
        self.assertEqual(calls, ["하나.", "하나. 둘."])  # always the whole text so far

    async def test_a_later_correction_of_the_whole_text_moves_an_earlier_mark(self):
        answers = {"회의는 세 시에": "회의는 세 시에.", "회의는 세 시에 시작합니다": "회의는 세 시에 시작합니다."}
        live = self.make(answers.get)
        live.update("회의는 세 시에")
        live.endpoint("회의는 세 시에")
        await until(lambda: self.writes and self.writes[-1] == "회의는 세 시에.")
        live.update("회의는 세 시에 시작합니다")
        self.assertEqual(await live.finish("회의는 세 시에 시작합니다"), "회의는 세 시에 시작합니다.")
        self.assertEqual(self.writes[-1], "회의는 세 시에 시작합니다.")

    async def test_speech_within_the_pause_leaves_one_correction_of_the_whole(self):
        calls = []
        def polish(text):
            calls.append(text)
            return text + "."
        live = self.make(polish)
        live.update("앞 말")
        live.endpoint("앞 말")
        live.update("앞 말 뒤 말")  # speaking again before the pause is over
        await asyncio.sleep(0.15)
        self.assertEqual(calls, [])
        self.assertEqual(await live.finish("앞 말 뒤 말"), "앞 말 뒤 말.")
        self.assertEqual(calls, ["앞 말 뒤 말"])

    async def test_a_rejected_correction_keeps_the_earlier_one(self):
        answers = {"하나": "하나.", "하나 둘": None}  # None: the model touched a letter
        live = self.make(answers.get)
        live.update("하나")
        live.endpoint("하나")
        await until(lambda: live.processed == "하나")
        live.update("하나 둘")
        self.assertEqual(await live.finish("하나 둘"), "하나. 둘")
        self.assertEqual(self.errors, [])

    async def test_a_changed_field_is_never_written_again(self):
        calls = []
        def moved(text):
            calls.append(text)
            return False
        live = self.make(write=moved)
        live.update("초안")
        await until(lambda: live.blocked)
        live.update("새 초안")
        self.assertEqual(await live.finish("새 초안"), "새 초안")
        self.assertEqual(calls, ["초안"])
        self.assertEqual(len(self.errors), 1)

    async def test_after_a_user_edit_later_speech_starts_at_the_new_caret(self):
        fields, started, release = [[]], threading.Event(), threading.Event()
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
            if text == "지울 문장.":
                started.set()
                release.wait(2)
            return text.replace("말한다", "말한다.")
        self.errors = []
        live = LiveDictation(write, polish, lambda text: text, self.errors.append, restart)
        self.addAsyncCleanup(live.close)
        live.update("지울 문장.")
        live.endpoint("지울 문장.")
        await until(started.is_set)
        await until(lambda: fields[0])
        live.update("지울 문장. 다시 말한다")
        await until(lambda: len(fields) == 2 and fields[1])
        release.set()
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
        await until(lambda: live.processed == "hello.")
        live.update("hello. new")
        self.assertEqual(await live.finish("hello. new"), "HELLO. NEW")
