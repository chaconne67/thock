"""Real Windows Edit control: UIA ranges and SendInput delivery, no user data or network."""
import sys
import threading
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "win32", "Windows desktop")
class WindowsInline(unittest.TestCase):
    control_class = "EDIT"
    control_dll = None

    @classmethod
    def setUpClass(cls):
        import ctypes
        from ctypes import wintypes as wt
        from thock.win32 import user32, kernel32
        cls.ctypes, cls.user32 = ctypes, user32
        if cls.control_dll:
            cls.loaded_control = ctypes.WinDLL(cls.control_dll)
        user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
        user32.CreateWindowExW.restype = wt.HWND
        user32.SetFocus.argtypes = [wt.HWND]
        user32.SetFocus.restype = wt.HWND
        user32.SetForegroundWindow.argtypes = [wt.HWND]
        user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        user32.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
        user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        cls.ready = threading.Event()
        cls.failed = None
        def gui():
            try:
                cls.window = user32.CreateWindowExW(0, "STATIC", "Thock input verification",
                                                    0x10CF0000, 200, 200, 640, 220, None, None, None, None)
                cls.edit = user32.CreateWindowExW(0, cls.control_class, "", 0x50801004,
                                                  15, 15, 590, 150, cls.window, None, None, None)
                if not cls.window or not cls.edit:
                    raise RuntimeError("test editor creation failed")
                user32.SetForegroundWindow(cls.window)
                user32.SetFocus(cls.edit)
                cls.ready.set()
                msg = wt.MSG()
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            except Exception as error:
                cls.failed = error
                cls.ready.set()
        cls.thread = threading.Thread(target=gui, daemon=True)
        cls.thread.start()
        if not cls.ready.wait(10) or cls.failed:
            raise RuntimeError("test editor did not start") from cls.failed

    @classmethod
    def tearDownClass(cls):
        cls.user32.PostMessageW(cls.window, 0x0010, 0, 0)

    def setUp(self):
        from thock.win32 import capture_target
        self.user32.SetWindowTextW(self.edit, "앞  뒤")
        self.user32.SendMessageW(self.edit, 0x00B1, 2, 2)  # EM_SETSEL
        self.target = capture_target()
        self.assertIsNotNone(self.target, "test editor must own foreground focus")
        self.assertEqual(self.target[0], self.window, "test editor must be foreground")
        from thock.editwatch import field_reader
        self.assertEqual(field_reader().snapshot(), ("앞 ", "", " 뒤"))

    def content(self):
        buf = self.ctypes.create_unicode_buffer(1024)
        self.user32.GetWindowTextW(self.edit, buf, len(buf))
        return buf.value

    def test_insert_revise_unicode_and_preserve_both_sides(self):
        from thock.win32 import InlineField
        field = InlineField(self.target)
        self.assertFalse(field.stopped)
        self.assertTrue(field.update("회의 하자"))
        self.assertEqual(self.content(), "앞 회의 하자 뒤")
        self.assertTrue(field.update("회의하자. 👍"))
        self.assertEqual(self.content(), "앞 회의하자. 👍 뒤")
        self.assertTrue(field.update("회의하자. 👍\r\n다음 줄"), field.failure)
        self.assertEqual(self.content(), "앞 회의하자. 👍\r\n다음 줄 뒤")
        self.assertTrue(field.update("회의하자."))
        self.assertEqual(self.content(), "앞 회의하자. 뒤")

    def test_direct_edit_prevents_late_correction(self):
        from thock.win32 import InlineField
        field = InlineField(self.target)
        self.assertTrue(field.update("초안"))
        self.user32.SetWindowTextW(self.edit, "사용자가 직접 고친 글")
        self.assertFalse(field.update("늦게 온 교정"))
        self.assertEqual(self.content(), "사용자가 직접 고친 글")

    def test_caret_move_preserves_current_document(self):
        from thock.win32 import InlineField
        field = InlineField(self.target)
        self.assertTrue(field.update("초안"))
        self.user32.SendMessageW(self.edit, 0x00B1, 0, 0)
        self.assertFalse(field.update("교정"))
        self.assertEqual(self.content(), "앞 초안 뒤")


    def test_empty_field_first_word_and_revision(self):
        from thock.win32 import InlineField, capture_target
        self.user32.SetWindowTextW(self.edit, "")
        self.user32.SendMessageW(self.edit, 0x00B1, 0, 0)
        field = InlineField(capture_target())
        self.assertFalse(field.stopped)
        self.assertTrue(field.update("지"), field.failure)
        self.assertEqual(self.content(), "지")
        self.assertTrue(field.update("지금 시작"), field.failure)
        self.assertTrue(field.update("지금 시작하자."), field.failure)
        self.assertEqual(self.content(), "지금 시작하자.")


class WindowsRichEdit(WindowsInline):
    control_class = "RICHEDIT50W"
    control_dll = "Msftedit.dll"


class WindowsRichEdit20(WindowsInline):
    control_class = "RichEdit20W"
    control_dll = "Riched20.dll"


class WindowsUIAInline(WindowsRichEdit):
    def setUp(self):
        super().setUp()
        from thock.editwatch import field_reader
        native = patch.object(field_reader(), "native_selection", return_value=None)
        native.start()
        self.addCleanup(native.stop)
