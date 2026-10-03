"""Windows: key hook, foreground app, clipboard, paste."""

import ctypes
import re
import threading
import ctypes.wintypes as wt
import time
from collections import deque
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
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL
kernel32.CreateMutexW.restype = wt.HANDLE

WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 13, 0x100, 0x101, 0x104, 0x105
WM_TIMER = 0x113
VK_SHIFT, VK_CONTROL, VK_V, KEYEVENTF_KEYUP, INPUT_KEYBOARD = 0x10, 0x11, 0x56, 2, 1
VK_RETURN, VK_MENU, VK_ESCAPE = 0x0D, 0x12, 0x1B
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 2
GDI_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}  # handles that are not global memory
HOOK_REARM_MS = 30_000
OWN_INPUT = 0x54484F434B
_input_revision = 0
_click_revision = 0  # the clicks among the user's inputs
_input_tracking = False


def allow_next_to_front():
    """Let the next window that asks come to the front, such as the browser opened for sign-in.
    Windows only lets the app in front hand the front on; a moment of Alt lifts that, as Alt-Tab does."""
    alt = [INPUT(INPUT_KEYBOARD, INPUT._U(ki=KEYBDINPUT(VK_MENU, 0, flags, 0, OWN_INPUT)))
           for flags in (0, KEYEVENTF_KEYUP)]
    user32.SendInput(1, (INPUT * 1)(alt[0]), ctypes.sizeof(INPUT))
    user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    user32.SendInput(1, (INPUT * 1)(alt[1]), ctypes.sizeof(INPUT))


input_events = deque(maxlen=300)  # (time, kind) of the user's own key and click events, for the trace


def press_enter():
    """The Enter held during a dictation, pressed for the user; marked as the app's own input."""
    keys = [INPUT(INPUT_KEYBOARD, INPUT._U(ki=KEYBDINPUT(VK_RETURN, 0, flags, 0, OWN_INPUT)))
            for flags in (0, KEYEVENTF_KEYUP)]
    user32.SendInput(len(keys), (INPUT * len(keys))(*keys), ctypes.sizeof(INPUT))


def run_key_hook(get_vk, on_key, hold_enter=lambda: False):
    """Swallow the hotkey (Shift+hotkey keeps its normal meaning) and report presses. Enter (not Shift+Enter)
    during a dictation is held too: the app presses it once the last words are written. Blocks forever."""
    global _input_tracking
    state = {"down": False, "passthrough": False, "enter": False}

    def proc(code, wparam, lparam):
        global _input_revision
        if code == 0:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if info.dwExtraInfo != OWN_INPUT and info.vkCode != get_vk():
                down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if info.vkCode == VK_RETURN and (state["enter"] or (
                        down and not user32.GetAsyncKeyState(VK_SHIFT) & 0x8000 and hold_enter())):
                    # Held, not the user's edit: the dictation finishes writing and then presses Enter.
                    if down and not state["enter"]:
                        on_key("enter")
                    state["enter"] = down
                    return 1
                _input_revision += 1
                input_events.append((time.perf_counter(), "key_down" if down else "key_up"))
                if down and info.vkCode == VK_ESCAPE:
                    on_key("escape")  # reported, not swallowed: the app under it gets its Esc too
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
        global _input_revision, _click_revision
        if code == 0 and wparam in (0x0201, 0x0204, 0x0207, 0x020B):
            # MSLLHOOKSTRUCT's POINT has the same layout as MOUSEINPUT's two LONGs.
            info = ctypes.cast(lparam, ctypes.POINTER(MOUSEINPUT)).contents
            if info.dwExtraInfo != OWN_INPUT:
                _input_revision += 1
                _click_revision += 1
                input_events.append((time.perf_counter(), "click"))
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
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise ctypes.WinError(ctypes.get_last_error())
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        raise ctypes.WinError(ctypes.get_last_error())


_clipboard_lock = threading.Lock()


def copy_text(text):
    """Put saved dictation on the clipboard for the user to paste."""
    with _clipboard_lock:
        _open_clipboard()
        try:
            user32.EmptyClipboard()
            _set_clipboard(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
            _set_clipboard(user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing"), b"\0")
        finally:
            user32.CloseClipboard()


def capture_target():
    from .editwatch import field_reader
    revision = _input_revision
    window = user32.GetForegroundWindow()
    identity = field_reader().focus_id()
    if (not window or not identity or window != user32.GetForegroundWindow()
            or revision != _input_revision):
        return None
    return window, identity, revision


READY_WAIT = 0.5  # a Chromium app idle for long answers the first question with its window, not its field


def clicks():
    return _click_revision


def ready_target():
    """(target, None, element) for the focused text field to dictate into, or (None, why not, element)
    when no editable, readable field has the caret; element describes the focus for the trace.
    A field not found is asked again for a moment: the first question wakes such an app up. Then a window
    with exactly one field to write into gets its focus there, and that field is asked for in turn."""
    from .editwatch import field_reader
    reader, deadline, moved = field_reader(), time.perf_counter() + READY_WAIT, False
    while True:
        target, element = capture_target(), reader.describe()
        why = ("no focus" if not target else "read only" if reader.read_only()
               else "unreadable" if reader.snapshot() is None else None)
        if why in (None, "read only") or time.perf_counter() >= deadline:
            if why and not moved and reader.focus_only_field(user32.GetForegroundWindow()):
                moved, deadline = True, time.perf_counter() + READY_WAIT
                continue
            return (None if why else target), why, element
        time.sleep(0.05)


def shown(field, text):
    """How the field shows Thock's own text right before an empty caret, or None when it does not. A rich editor
    turns a blank line into a paragraph break and reads it back as one line break, so a run of line breaks may
    come back shorter; every other character must be as written. The rest of the field is the editor's: an
    empty-field cue or a trailing line break it shows or removes as typing starts is not checked."""
    if field is None or field[1]:
        return None
    if field[0].endswith(text):
        return text
    want = re.sub(r"\n+", "\n", text)
    for length in range(len(want), len(text)):
        tail = field[0][len(field[0]) - length:]
        if re.sub(r"\n+", "\n", tail) == want:
            return tail
    return None


def shows(field, text):
    """Thock's own text sits right before an empty caret (see shown)."""
    return shown(field, text) is not None


class InlineField:
    """Own exactly the selected range and the text subsequently inserted there."""
    def __init__(self, target, mark=None):
        from .editwatch import field_reader
        self.target, self.current, self.written = target, None, None  # current: as the field shows written
        self.attempt = None  # the last text a write tried to show
        self.mark = mark or (lambda name, **values: None)
        self.revision, self.clicks = _input_revision, _click_revision
        self.mismatch = None  # diagnosis: how the field differed when it never showed the paste (lengths only)
        # Before the first write the field must still be as found; afterwards this is only its last seen shape.
        self.initial = field_reader().snapshot() if target and capture_target() == target else None
        self.stopped = self.initial is None
        self.failure = "range_unavailable" if self.stopped else None
        self.mark("field", **(dict(zip(("before", "selected", "after"), map(len, self.initial)))
                              if self.initial else {"none": 1}))

    def restart(self):
        """After the user's own edit, own the range at the current caret."""
        if self.failure != "user_input":
            return False
        self.__init__(capture_target(), self.mark)
        return not self.stopped

    def resume(self):
        """After a pause: None while the dictation's field is not in front with a readable caret. Otherwise
        writing goes on there: "same" when it shows Thock's text as left (or the write that was under way),
        "moved" when the user changed it, from their caret then. A key or click meanwhile was elsewhere."""
        from .editwatch import field_reader
        target = capture_target()
        if not target or target[:2] != self.target[:2]:
            return None
        seen = field_reader().snapshot()
        if seen is None:
            return None
        self.target, self.revision, self.clicks = target, _input_revision, _click_revision
        self.failure, self.mismatch, self.stopped = None, None, False
        landed = shown(seen, self.attempt) if self.attempt is not None else None
        if landed is not None:
            self.current, self.written = landed, self.attempt
            self.initial = seen[0][:len(seen[0]) - len(landed)], "", seen[2]
            return "same"
        if seen == self.initial if self.current is None else shows(seen, self.current):
            return "same"
        self.__init__(target, self.mark)
        return None if self.stopped else "moved"

    def _refuse(self, failure):
        self.failure, self.stopped = failure, True
        self.mark("refused", why=failure, shape=self.mismatch)
        return False

    def update(self, text):
        from .editwatch import field_reader, normalize_newlines
        text = normalize_newlines(text)
        if self.stopped:
            return False
        if _input_revision != self.revision:
            # Clicks alone that left the field, its text and its caret as Thock left them are not an edit: the
            # final correction still goes in (Crema 2026-10-03 12:02, two clicks while correcting). A key is.
            clicks_only = _input_revision - self.revision == _click_revision - self.clicks
            target, seen = capture_target(), field_reader().snapshot()
            if not (clicks_only and target and target[:2] == self.target[:2]
                    and (seen == self.initial if self.current is None else shows(seen, self.current))):
                return self._refuse("user_input")
            self.target, self.revision, self.clicks = target, _input_revision, _click_revision
        if capture_target() != self.target:
            return self._refuse("focus_changed")
        reader = field_reader()
        seen = reader.snapshot()
        owned = seen == self.initial if self.current is None else shows(seen, self.current)
        if not owned:
            self.mismatch = mismatch(seen, self.initial if self.current is None
                                     else (self.initial[0] + self.current, "", self.initial[2]))
            return self._refuse("content_or_caret_changed")
        if text == self.written:
            return True
        expected, common = seen, 0
        if self.current is not None:
            for old, new in zip(self.current, text):
                if old != new:
                    break
                common += 1
            tail = self.current[common:]
            if not tail and common == len(text):
                return True
            selecting = time.monotonic()
            expected = reader.select_tail(seen, tail)
            self.mark("select", tail=len(tail), ms=round((time.monotonic() - selecting) * 1000))
            if expected is None:
                return self._refuse("selection_unavailable")
        started = time.monotonic()
        self.attempt = text
        actual = paste(text[common:], self.target, expected, text)
        self.mark("write", length=len(text), kept=common, ms=round((time.monotonic() - started) * 1000),
                  ok=int(actual is not None and _input_revision == self.revision))
        if actual is None or _input_revision != self.revision:
            self.failure = ("delivery_user_input" if _input_revision != self.revision
                            else "delivery_focus_changed" if capture_target() != self.target
                            else "delivery_unverified")
            self.stopped = True
            if self.failure == "delivery_unverified":
                self.mismatch = mismatch(reader.snapshot(), (expected[0] + text[common:], "", expected[2]))
            self.mark("refused", why=self.failure, shape=self.mismatch)
            return False
        self.current, self.written = shown(actual, text), text
        self.initial = actual[0][:len(actual[0]) - len(self.current)], "", actual[2]
        return True


def mismatch(actual, desired):
    """Where a field differs from what the paste should have made, in lengths only: before/selected/after
    wanted and seen, and how many characters agree from the start and from the end."""
    if actual is None:
        return "unreadable"
    seen, want = "".join(actual), "".join(desired)
    start = next((i for i, (a, b) in enumerate(zip(seen, want)) if a != b), min(len(seen), len(want)))
    end = next((i for i, (a, b) in enumerate(zip(reversed(seen), reversed(want))) if a != b), min(len(seen), len(want)))
    return ("want %d/%d/%d seen %d/%d/%d same_start %d same_end %d"
            % (*map(len, desired), *map(len, actual), start, end))


def paste(text, target, expected, written):
    """Paste at the verified range, wait until the field shows written (all of Thock's text) right before
    the caret, and restore an unchanged clipboard."""
    from .editwatch import APPLY_SECONDS, field_reader
    revision = _input_revision
    reader = field_reader()
    def unchanged():
        return (_input_revision == revision and target and capture_target() == target
                and (expected is None or reader.snapshot() == expected))
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
            deadline = time.monotonic() + APPLY_SECONDS
            while time.monotonic() < deadline:
                time.sleep(0.015)
                if _input_revision != revision or capture_target() != target:
                    return None
                observed = reader.snapshot()
                if observed != expected and shows(observed, written):  # changed: the field may have ended so already
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
