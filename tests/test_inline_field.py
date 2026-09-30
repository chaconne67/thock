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
        self.paste_patch = patch("thock.win32.paste", return_value=True)
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
                                     ("앞 안녕", " 하세요", " 뒤"), ("앞 안녕하세요", "", " 뒤"))

    def test_existing_user_selection_is_the_only_initial_replacement(self):
        self.reader.snapshot.return_value = ("앞 ", "선택한 글", " 뒤")
        field = self.field()
        self.assertTrue(field.update("새 글"))
        self.paste.assert_called_once_with("새 글", self.target,
                                          ("앞 ", "선택한 글", " 뒤"), ("앞 새 글", "", " 뒤"))

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

    def test_lost_focus_cannot_paste_into_a_different_field(self):
        field = self.field()
        with patch("thock.win32.capture_target", return_value=(12, (99,))):
            self.assertFalse(field.update("글"))
        self.paste.assert_not_called()
