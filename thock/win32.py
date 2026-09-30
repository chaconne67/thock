"""Windows: key hook, foreground app, clipboard, paste."""

import ctypes
import threading
import ctypes.wintypes as wt
import time
from pathlib import Path


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _fields_ = [("type", wt.DWORD), ("u", _U)]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.OpenClipboard.argtypes = [wt.HWND]
user32.EnumClipboardFormats.argtypes = [wt.UINT]
user32.EnumClipboardFormats.restype = wt.UINT
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
user32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wt.UINT
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.CreateMutexW.restype = wt.HANDLE

WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 13, 0x100, 0x101, 0x104, 0x105
WM_TIMER = 0x113
VK_SHIFT, VK_CONTROL, VK_V, KEYEVENTF_KEYUP, INPUT_KEYBOARD = 0x10, 0x11, 0x56, 2, 1
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 2
GDI_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}  # handles that are not global memory
HOOK_REARM_MS = 30_000
OWN_INPUT = 0x54484F434B
_input_revision = 0
_input_tracking = False


def run_key_hook(get_vk, on_key):
    """Swallow the hotkey (Shift+hotkey keeps its normal meaning) and report presses. Blocks forever."""
    global _input_tracking
    state = {"down": False, "passthrough": False}

    def proc(code, wparam, lparam):
        global _input_revision
        if code == 0:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if info.dwExtraInfo != OWN_INPUT and info.vkCode != get_vk():
                _input_revision += 1
            if info.vkCode == get_vk():
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if not state["down"]:
                        state["down"] = True
                        state["passthrough"] = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
                        if not state["passthrough"]:
                            on_key("down")
                    if not state["passthrough"]:
                        return 1
                elif wparam in (WM_KEYUP, WM_SYSKEYUP):
                    state["down"] = False
                    if not state["passthrough"]:
                        on_key("up")
                        return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def mouse_proc(code, wparam, lparam):
        global _input_revision
        if code == 0 and wparam in (0x0201, 0x0204, 0x0207, 0x020B):
            # MSLLHOOKSTRUCT's POINT has the same layout as MOUSEINPUT's two LONGs.
            info = ctypes.cast(lparam, ctypes.POINTER(MOUSEINPUT)).contents
            if info.dwExtraInfo != OWN_INPUT:
                _input_revision += 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    callback, mouse_callback = HOOKPROC(proc), HOOKPROC(mouse_proc)

    def install(kind, callback):
        return user32.SetWindowsHookExW(kind, callback, kernel32.GetModuleHandleW(None), 0)

    hook = install(WH_KEYBOARD_LL, callback)
    mouse_hook = install(14, mouse_callback)  # WH_MOUSE_LL
    _input_tracking = bool(hook and mouse_hook)
    if not hook:
        raise ctypes.WinError(ctypes.get_last_error())
    user32.SetTimer(None, 0, HOOK_REARM_MS, None)
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_TIMER:
            # Windows silently drops a low-level hook whose callback was ever too slow (the usual
            # "hotkey suddenly stopped working" bug). Re-arming keeps CapsLock alive without a restart.
            if fresh := install(WH_KEYBOARD_LL, callback):
                user32.UnhookWindowsHookEx(hook)
                hook = fresh
            if fresh := install(14, mouse_callback):
                if mouse_hook:
                    user32.UnhookWindowsHookEx(mouse_hook)
                mouse_hook = fresh
            _input_tracking = bool(hook and mouse_hook)


def foreground_app():
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return "unknown"
    try:
        buf, size = ctypes.create_unicode_buffer(260), wt.DWORD(260)
        kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return Path(buf.value).name or "unknown"
    finally:
        kernel32.CloseHandle(handle)


def _open_clipboard():
    for _ in range(50):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.02)
    raise RuntimeError("clipboard is busy")


def _set_clipboard(fmt, data):
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(len(data), 1))
    ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        raise ctypes.WinError(ctypes.get_last_error())



_clipboard_lock = threading.Lock()


def capture_target():
    from .editwatch import field_reader
    revision = _input_revision
    window = user32.GetForegroundWindow()
    identity = field_reader().focus_id()
    if (not window or not identity or window != user32.GetForegroundWindow()
            or revision != _input_revision):
        return None
    return window, identity, revision


class InlineField:
    """Own exactly the selected range and the text subsequently inserted there."""
    def __init__(self, target):
        from .editwatch import field_reader
        self.target, self.current = target, None
        self.initial = field_reader().snapshot() if target and capture_target() == target else None
        self.stopped = self.initial is None
        self.failure = "range_unavailable" if self.stopped else None

    def update(self, text):
        from .editwatch import field_reader, normalize_newlines
        text = normalize_newlines(text)
        if self.stopped:
            return False
        if _input_revision != self.target[-1]:
            self.failure, self.stopped = "user_input", True
            return False
        if capture_target() != self.target:
            self.failure, self.stopped = "focus_changed", True
            return False
        before, selected, after = self.initial
        expected = self.initial if self.current is None else (before + self.current, "", after)
        reader = field_reader()
        if reader.snapshot() != expected:
            self.failure, self.stopped = "content_or_caret_changed", True
            return False
        common = 0
        if self.current is not None:
            for old, new in zip(self.current, text):
                if old != new:
                    break
                common += 1
            tail = self.current[common:]
            if not tail and common == len(text):
                return True
            expected = reader.select_tail(expected, tail)
            if expected is None:
                self.failure, self.stopped = "selection_unavailable", True
                return False
        desired = (before + text, "", after)
        # Some native editors expose a cue as document text, then remove it on first input.
        # Adopt that result only when it contains solely our text and no other input intervened.
        cue = (self.current is None and not before and not selected and bool(after)
               and _input_tracking and reader.native_selection() is not None)
        actual = self._paste(text[common:], expected, desired, cue)
        if actual is None or _input_revision != self.target[-1]:
            self.failure, self.stopped = "delivery_unverified", True
            return False
        self.initial = before, selected, actual[2]
        self.current = text
        return True

    def _paste(self, text, expected, desired, allow_cue=False):
        """Paste at the verified range, wait for delivery, and restore an unchanged clipboard."""
        from .editwatch import field_reader
        reader = field_reader()
        def unchanged():
            return capture_target() == self.target and reader.snapshot() == expected
        if not unchanged():
            return None
        with _clipboard_lock:
            _open_clipboard()
            try:
                saved, fmt = [], user32.EnumClipboardFormats(0)
                while fmt:
                    handle = user32.GetClipboardData(fmt) if fmt not in GDI_FORMATS else None
                    if handle and (size := kernel32.GlobalSize(handle)):
                        saved.append((fmt, ctypes.string_at(kernel32.GlobalLock(handle), size)))
                        kernel32.GlobalUnlock(handle)
                    fmt = user32.EnumClipboardFormats(fmt)
                user32.EmptyClipboard()
                _set_clipboard(CF_UNICODETEXT, (text.replace("\n", "\r\n") + "\0").encode("utf-16-le"))
                _set_clipboard(user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing"), b"\0")
            finally:
                user32.CloseClipboard()
            sequence = user32.GetClipboardSequenceNumber()
            keys = ([(VK_CONTROL, 0), (VK_V, 0), (VK_V, KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP)]
                    if text else [(0x08, 0), (0x08, KEYEVENTF_KEYUP)])
            inputs = (INPUT * len(keys))(*[
                INPUT(INPUT_KEYBOARD, INPUT._U(ki=KEYBDINPUT(vk, 0, flags, 0, OWN_INPUT))) for vk, flags in keys])
            try:
                if not unchanged():
                    return None
                if user32.SendInput(len(keys), inputs, ctypes.sizeof(INPUT)) != len(keys):
                    raise ctypes.WinError(ctypes.get_last_error())
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    time.sleep(0.015)
                    target = capture_target()
                    if user32.GetForegroundWindow() != self.target[0] or _input_revision != self.target[-1]:
                        return None
                    if target is None:
                        continue
                    observed = reader.snapshot()
                    if (observed == desired or allow_cue and observed == (text, "", "")) and capture_target() == target:
                        # Web editors may rebuild their accessibility element during our paste.
                        # Adopt it only after exact content/caret delivery with no intervening input.
                        self.target = target
                        return observed
                return None
            finally:
                _open_clipboard()
                try:
                    if user32.GetClipboardSequenceNumber() == sequence:
                        user32.EmptyClipboard()
                        for fmt, data in saved:
                            _set_clipboard(fmt, data)
                finally:
                    user32.CloseClipboard()
