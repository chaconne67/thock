"""Windows: key hook, foreground app, clipboard, paste."""

import ctypes
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


def run_key_hook(get_vk, on_key):
    """Swallow the hotkey (Shift+hotkey keeps its normal meaning) and report presses. Blocks forever."""
    state = {"down": False, "passthrough": False}

    def proc(code, wparam, lparam):
        if code == 0:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
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

    callback = HOOKPROC(proc)

    def install():
        return user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, kernel32.GetModuleHandleW(None), 0)

    hook = install()
    if not hook:
        raise ctypes.WinError(ctypes.get_last_error())
    user32.SetTimer(None, 0, HOOK_REARM_MS, None)
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_TIMER:
            # Windows silently drops a low-level hook whose callback was ever too slow (the usual
            # "hotkey suddenly stopped working" bug). Re-arming keeps CapsLock alive without a restart.
            if fresh := install():
                user32.UnhookWindowsHookEx(hook)
                hook = fresh


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
    user32.SetClipboardData(fmt, handle)


def paste(text):
    """Paste text at the cursor, then put the previous clipboard contents back."""
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
        _set_clipboard(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
        # Keep dictated text out of clipboard history and clipboard managers.
        _set_clipboard(user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing"), b"\0")
    finally:
        user32.CloseClipboard()

    keys = [(VK_CONTROL, 0), (VK_V, 0), (VK_V, KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP)]
    inputs = (INPUT * len(keys))(*[INPUT(INPUT_KEYBOARD, INPUT._U(ki=KEYBDINPUT(vk, 0, flags, 0, 0))) for vk, flags in keys])
    try:
        if user32.SendInput(len(keys), inputs, ctypes.sizeof(INPUT)) != len(keys):
            raise ctypes.WinError(ctypes.get_last_error())
        time.sleep(0.4)  # the target app reads the clipboard asynchronously
    finally:
        _open_clipboard()
        try:
            user32.EmptyClipboard()
            for fmt, data in saved:
                _set_clipboard(fmt, data)
        finally:
            user32.CloseClipboard()
