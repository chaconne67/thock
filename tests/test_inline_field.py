import sys
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(sys.platform == "win32", "Windows input ranges")
class InlineFieldContract(unittest.TestCase):
    def setUp(self):
        self.target = (12, (34,))
        self.reader = Mock()
        self.reader.snapshot.return_value = ("앞 ", "", " 뒤")
        self.reader.select_tail.side_effect = lambda expected, tail: (
            expected[0][:-len(tail)], tail, expected[2]) if tail else expected
        for item in (
                patch("thock.win32.capture_target", return_value=self.target),
                patch("thock.editwatch.field_reader", return_value=self.reader)):
            item.start()
            self.addCleanup(item.stop)
        self.paste_patch = patch("thock.win32.paste", side_effect=lambda text, target, expected, desired, cue: desired)
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
        self.paste.assert_called_with("하세요", self.target,
                                     ("앞 안녕", " 하세요", " 뒤"), ("앞 안녕하세요", "", " 뒤"), False)

    def test_existing_user_selection_is_the_only_initial_replacement(self):
        self.reader.snapshot.return_value = ("앞 ", "선택한 글", " 뒤")
        field = self.field()
        self.assertTrue(field.update("새 글"))
        self.paste.assert_called_once_with("새 글", self.target,
                                          ("앞 ", "선택한 글", " 뒤"), ("앞 새 글", "", " 뒤"), False)

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
            self.assertEqual(self.paste.call_args.args[3], ("다음", "", ""))
        field = self.field()
        with patch("thock.win32.capture_target", return_value=(12, (99,))):
            self.assertFalse(field.update("글"))
        self.assertEqual(field.failure, "focus_changed")
        self.assertFalse(field.restart())

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


    def test_first_input_can_remove_a_native_cue_without_touching_other_text(self):
        self.reader.snapshot.return_value = ("", "", "editor cue")
        self.reader.native_selection.return_value = (123, ("", "", "editor cue"), 0, 0, "\r")
        with patch("thock.win32._input_tracking", True):
            field = self.field()
            self.paste.side_effect = lambda text, target, expected, desired, cue: (text, "", "") if cue else desired
            self.assertTrue(field.update("first"))
            self.assertEqual(field.initial, ("", "", ""))
            self.reader.snapshot.return_value = ("first", "", "")
            self.assertTrue(field.update("first corrected"))
            self.assertEqual(self.paste.call_args.args[3], ("first corrected", "", ""))

    def test_first_input_can_remove_a_web_editor_cue(self):
        # Chromium and Electron editors expose their empty-field cue through UI Automation too.
        self.reader.snapshot.return_value = ("", "", "\nplaceholder")
        self.reader.native_selection.return_value = None
        with patch("thock.win32._input_tracking", True):
            field = self.field()
            self.paste.side_effect = lambda text, target, expected, desired, cue: (text, "", "") if cue else None
            self.assertTrue(field.update("first"), field.failure)
            self.assertEqual(field.initial, ("", "", ""))

    def test_user_input_during_first_delivery_cannot_be_adopted_as_a_cue(self):
        import thock.win32 as win32
        self.reader.snapshot.return_value = ("", "", "existing text")
        with patch("thock.win32._input_revision", 0), patch("thock.win32._input_tracking", True):
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
