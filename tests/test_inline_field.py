import sys
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(sys.platform == "win32", "Windows input ranges")
class Mismatch(unittest.TestCase):
    def test_the_shape_of_a_field_that_never_showed_the_paste_has_lengths_only(self):
        from thock.win32 import mismatch
        shape = mismatch(("비밀 앞", "", "\n"), ("비밀 앞글", "", ""))
        self.assertEqual(shape, "want 5/0/0 seen 4/0/1 same_start 4 same_end 0")
        self.assertNotIn("비밀", shape)
        self.assertEqual(mismatch(None, ("a", "", "")), "unreadable")


@unittest.skipUnless(sys.platform == "win32", "Windows input ranges")
class InlineFieldContract(unittest.TestCase):
    def setUp(self):
        self.target = (12, (34,))
        self.reader = Mock()
        self.reader.snapshot.return_value = ("앞 ", "", " 뒤")
        self.reader.read_only.return_value = False
        self.reader.describe.return_value = "50004/test"
        self.reader.select_tail.side_effect = lambda expected, tail: (
            expected[0][:-len(tail)], tail, expected[2]) if tail else expected
        for item in (
                patch("thock.win32.capture_target", return_value=self.target),
                patch("thock.editwatch.field_reader", return_value=self.reader)):
            item.start()
            self.addCleanup(item.stop)
        self.paste_patch = patch("thock.win32.paste", side_effect=lambda text, target, expected, written: (
            expected[0] + text, "", expected[2]))
        self.paste = self.paste_patch.start()
        self.addCleanup(self.paste_patch.stop)

    def field(self):
        from thock.win32 import InlineField
        return InlineField(self.target)

    def test_rewrites_only_its_changed_suffix_in_the_middle(self):
        field = self.field()
        self.assertTrue(field.update("안녕 하세요"))
        self.reader.snapshot.return_value = ("앞 안녕 하세요", "", " 뒤")
        self.assertTrue(field.update("안녕하세요"))
        self.reader.select_tail.assert_called_once_with(("앞 안녕 하세요", "", " 뒤"), " 하세요")
        self.paste.assert_called_with("하세요", self.target, ("앞 안녕", " 하세요", " 뒤"), "안녕하세요")

    def test_a_blank_line_the_editor_makes_a_paragraph_break_is_still_thocks_text(self):
        # Claude's input (ProseMirror) reads a pasted blank line back as one line break (2026-10-02).
        self.paste.side_effect = lambda text, target, expected, written: (
            expected[0] + text.replace("\n\n", "\n"), "", expected[2])
        field = self.field()
        self.assertTrue(field.update("첫 문단.\n\n둘째 문단."))
        self.assertEqual((field.initial[0], field.current), ("앞 ", "첫 문단.\n둘째 문단."))
        self.reader.snapshot.return_value = ("앞 첫 문단.\n둘째 문단.", "", " 뒤")
        self.assertTrue(field.update("첫 문단.\n\n둘째 문단."))  # the same text: nothing is written again
        self.assertEqual(self.paste.call_count, 1)
        self.assertTrue(field.update("첫 문단.\n\n둘째 문단입니다."))
        self.reader.select_tail.assert_called_once_with(("앞 첫 문단.\n둘째 문단.", "", " 뒤"), "둘째 문단.")
        from thock.win32 import shows
        self.assertFalse(shows(("앞 첫 문단 둘째 문단.", "", ""), "첫 문단.\n\n둘째 문단."))  # other letters: not shown

    def test_existing_user_selection_is_the_only_initial_replacement(self):
        self.reader.snapshot.return_value = ("앞 ", "선택한 글", " 뒤")
        field = self.field()
        self.assertTrue(field.update("새 글"))
        self.paste.assert_called_once_with("새 글", self.target, ("앞 ", "선택한 글", " 뒤"), "새 글")

    def test_typing_or_moving_caret_permanently_stops_replacement(self):
        field = self.field()
        self.reader.snapshot.return_value = ("앞", "", "  뒤")
        self.assertFalse(field.update("초안"))
        self.reader.snapshot.return_value = ("앞 ", "", " 뒤")
        self.assertFalse(field.update("교정"))
        self.paste.assert_not_called()

    def test_failed_range_selection_never_falls_back_to_backspaces(self):
        field = self.field()
        self.assertTrue(field.update("반복 반복"))
        self.reader.snapshot.return_value = ("앞 반복 반복", "", " 뒤")
        self.reader.select_tail.side_effect = None
        self.reader.select_tail.return_value = None
        self.assertFalse(field.update("반복 교정"))
        self.assertEqual(self.paste.call_count, 1)

    def test_only_a_user_edit_moves_the_owned_range_to_the_current_caret(self):
        import thock.win32 as win32
        with patch("thock.win32._input_revision", 0):
            field = self.field()
            self.assertTrue(field.update("초안"))
            win32._input_revision += 1
            self.reader.snapshot.return_value = ("", "", "")
            self.assertFalse(field.update("초안 다음"))
            self.assertEqual(field.failure, "user_input")
            self.assertTrue(field.restart())
            self.assertEqual((field.current, field.initial), (None, ("", "", "")))
            self.assertTrue(field.update("다음"))
            self.assertEqual(self.paste.call_args.args[3], "다음")
        field = self.field()
        with patch("thock.win32.capture_target", return_value=(12, (99,))):
            self.assertFalse(field.update("글"))
        self.assertEqual(field.failure, "focus_changed")
        self.assertFalse(field.restart())

    def test_a_window_another_program_brought_up_is_put_back_twice_at_most(self):
        import thock.win32 as win32
        field = self.field()
        self.assertTrue(field.update("초안"))
        self.reader.snapshot.return_value = ("앞 초안", "", " 뒤")
        stolen = [(77, (5,), 0)]  # a console took the front; no key or click from the user
        def front():
            return stolen[0] if stolen else self.target
        brought = []
        def bring(window):
            brought.append(window)
            stolen.clear()
            return True
        with patch("thock.win32.capture_target", side_effect=front), patch("thock.win32.bring_to_front", side_effect=bring):
            self.assertTrue(field.update("초안 계속"))
            self.assertEqual(brought, [12])
            stolen.append((77, (5,), 0))
            self.reader.snapshot.return_value = ("앞 초안 계속", "", " 뒤")
            self.assertTrue(field.update("초안 계속 말"))
            stolen.append((77, (5,), 0))
            self.assertFalse(field.update("초안 계속 말한다"))  # a third time: two windows taking turns
        self.assertEqual((field.failure, brought), ("focus_changed", [12, 12]))
        with patch("thock.win32._input_revision", 1):  # the user's own key or click: no taking back
            field = self.field()
            win32._input_revision += 1
            self.assertFalse(field.update("글"))
            self.assertEqual(field.failure, "user_input")

    def test_a_field_without_a_readable_caret_stops_and_is_not_ready(self):
        from thock.win32 import InlineField, ready_target
        self.reader.snapshot.return_value = None
        field = InlineField(self.target)
        self.assertEqual((field.stopped, field.failure), (True, "range_unavailable"))
        self.assertFalse(field.update("글"))
        self.assertFalse(field.restart())  # only the user's edit moves the range
        self.assertEqual(ready_target(), (None, "unreadable", "50004/test"))  # a guide, not a recording
        self.reader.snapshot.return_value = ("", "", "")
        self.assertEqual(ready_target(), (self.target, None, "50004/test"))
        self.reader.read_only.return_value = True  # a web page's own text: readable, not writable
        self.assertEqual(ready_target()[:2], (None, "read only"))

    def test_a_field_an_idle_app_shows_a_moment_late_is_still_ready(self):
        from thock.win32 import ready_target
        self.reader.snapshot.side_effect = [None, None, ("", "", "")]  # the first question wakes the app up
        self.assertEqual(ready_target(), (self.target, None, "50004/test"))

    def test_failed_delivery_names_what_changed(self):
        import thock.win32 as win32
        self.paste.side_effect = lambda *args: None
        with patch("thock.win32._input_revision", 0):
            field = self.field()
            with patch("thock.win32.capture_target", side_effect=[self.target, (12, (99,))]):
                self.assertFalse(field.update("글"))
            self.assertEqual(field.failure, "delivery_focus_changed")
            field = self.field()
            self.paste.side_effect = lambda *args: win32.__dict__.__setitem__("_input_revision", 1)
            self.assertFalse(field.update("글"))
            self.assertEqual(field.failure, "delivery_user_input")
            self.assertFalse(field.restart())  # only an edit before a write moves the range
        field = self.field()
        self.paste.side_effect = lambda *args: None
        self.assertFalse(field.update("글"))
        self.assertEqual(field.failure, "delivery_unverified")

    def test_lost_focus_cannot_paste_into_a_different_field(self):
        field = self.field()
        with patch("thock.win32.capture_target", return_value=(12, (99,))):
            self.assertFalse(field.update("글"))
        self.paste.assert_not_called()


    def test_the_editors_own_text_around_the_caret_may_go_on_the_first_input(self):
        # Empty-field cues: Grok's sits before the caret, Codex's and Claude's after it, a new list item's
        # line break after it. Each goes when typing starts; only Thock's own text is checked.
        for found in (("무엇이든 물어보세요", "", "\n"), ("", "", "\nplaceholder"), ("1. 앞 문장\n", "", "\n")):
            with self.subTest(found=found):
                self.reader.snapshot.return_value = found
                field = self.field()
                self.paste.side_effect = lambda text, target, expected, written: (written, "", "")
                self.assertTrue(field.update("중앙"), field.failure)
                self.assertEqual(field.initial, ("", "", ""))
                self.reader.snapshot.return_value = ("중앙", "", "")
                self.paste.side_effect = lambda text, target, expected, written: (written, "", "")
                self.assertTrue(field.update("중앙 정부"), field.failure)
                self.assertEqual(self.paste.call_args.args[::3], (" 정부", "중앙 정부"))

    def test_a_change_to_thocks_own_text_or_a_selection_over_it_stops_writing(self):
        for seen in (("앞 초안을", "", " 뒤"), ("앞 초", "안", " 뒤"), ("앞 ", "", "초안 뒤")):
            with self.subTest(seen=seen):
                self.reader.snapshot.return_value = ("앞 ", "", " 뒤")
                field = self.field()
                self.assertTrue(field.update("초안"))
                self.reader.snapshot.return_value = seen
                calls = self.paste.call_count
                self.assertFalse(field.update("초안 다음"))
                self.assertEqual(field.failure, "content_or_caret_changed")
                self.assertEqual(self.paste.call_count, calls)

    def test_text_outside_thocks_own_may_change_without_stopping_it(self):
        field = self.field()
        self.assertTrue(field.update("초안"))
        self.reader.snapshot.return_value = ("1. 앞 초안", "", " 뒤에 붙은 글")  # e.g. the editor made a list
        self.assertTrue(field.update("초안 다음"), field.failure)
        self.assertEqual(self.paste.call_args.args[2], ("1. 앞 초안", "", " 뒤에 붙은 글"))

    def test_user_input_during_first_delivery_stops_writing(self):
        import thock.win32 as win32
        self.reader.snapshot.return_value = ("", "", "existing text")
        with patch("thock.win32._input_revision", 0):
            field = self.field()
            def delivered(*args):
                win32._input_revision += 1
                return ("first", "", "")
            self.paste.side_effect = delivered
            self.assertFalse(field.update("first"))
            self.assertFalse(field.update("late correction"))
            self.assertEqual(self.paste.call_count, 1)

    def test_input_activity_stops_even_when_the_caret_returns_to_the_same_place(self):
        import thock.win32 as win32
        with patch("thock.win32._input_revision", 0):
            field = self.field()
            win32._input_revision += 1
            self.assertFalse(field.update("must not be typed"))
            self.paste.assert_not_called()
