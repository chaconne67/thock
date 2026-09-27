"""After a paste, re-read the text field through UI Automation and record the words the user fixed."""

import ctypes
import queue
import threading
import time

from .config import log
from .learning import fixes_in_field


ole32, oleaut32 = ctypes.WinDLL("ole32"), ctypes.WinDLL("oleaut32")
oleaut32.SysStringLen.argtypes = [ctypes.c_void_p]
oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
_PP = ctypes.POINTER(ctypes.c_void_p)


class GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


def _guid(text):
    g = GUID()
    ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
    return g


def _com(obj, index, *argtypes):
    """Method `index` of a COM interface pointer (vtable order from UIAutomationClient.h)."""
    fn = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0][index]
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(fn)


def _release(obj):
    if obj:
        fn = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0][2]
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(fn)(obj)


def _bstr(b):
    try:
        return ctypes.wstring_at(b, oleaut32.SysStringLen(b)) if b else ""
    finally:
        oleaut32.SysFreeString(b)


class FieldReader:
    """Reads the focused text field of any app through UI Automation (COM, one thread only)."""
    IID_VALUE, IID_TEXT = _guid("{a94cd8b1-0844-4cd6-9d2d-640537ab39e9}"), _guid("{32eba289-3583-42c9-9c59-3b6d9a1e9b6a}")

    def __init__(self):
        ole32.CoInitializeEx(None, 0)
        self.uia = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(_guid("{ff48dba4-60ef-4201-aa87-54103eef594e}")), None, 1,
                               ctypes.byref(_guid("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")), ctypes.byref(self.uia))

    def read_focused(self):
        el = ctypes.c_void_p()
        try:
            _com(self.uia, 8, _PP)(self.uia, ctypes.byref(el))  # GetFocusedElement
            return self.read(el) if el else None
        except OSError:
            return None
        finally:
            _release(el)

    def read(self, el):
        """Field text, or None if it can no longer be read. Input boxes expose a value; editors and
        terminals expose their visible text."""
        try:
            pattern = ctypes.c_void_p()
            _com(el, 14, ctypes.c_int, ctypes.POINTER(GUID), _PP)(el, 10002, ctypes.byref(self.IID_VALUE), ctypes.byref(pattern))
            if pattern:
                try:
                    value = ctypes.c_void_p()
                    _com(pattern, 4, _PP)(pattern, ctypes.byref(value))  # get_CurrentValue
                    return _bstr(value.value)
                finally:
                    _release(pattern)
            _com(el, 14, ctypes.c_int, ctypes.POINTER(GUID), _PP)(el, 10014, ctypes.byref(self.IID_TEXT), ctypes.byref(pattern))
            if not pattern:
                return None
            try:
                ranges, count, parts = ctypes.c_void_p(), ctypes.c_int(), []
                _com(pattern, 6, _PP)(pattern, ctypes.byref(ranges))  # GetVisibleRanges
                try:
                    _com(ranges, 3, ctypes.POINTER(ctypes.c_int))(ranges, ctypes.byref(count))
                    for i in range(count.value):
                        rng, text = ctypes.c_void_p(), ctypes.c_void_p()
                        _com(ranges, 4, ctypes.c_int, _PP)(ranges, i, ctypes.byref(rng))
                        try:
                            _com(rng, 12, ctypes.c_int, _PP)(rng, -1, ctypes.byref(text))  # GetText
                            parts.append(_bstr(text.value))
                        finally:
                            _release(rng)
                finally:
                    _release(ranges)
                return "".join(parts)
            finally:
                _release(pattern)
        except OSError:
            return None


class EditWatcher:
    """After each paste, re-reads that field until the user sends it, moves on or starts the next
    dictation, then records the word fixes they made. Only fix pairs are kept, never the text."""
    POLL, LIMIT = 0.7, 90

    def __init__(self, notes):
        self.notes, self.jobs = notes, queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def watch(self, pasted):
        self.jobs.put(pasted)

    def flush(self):
        """Record what the user fixed so far (called when the next dictation starts)."""
        self.jobs.put(None)

    def _run(self):
        reader, job = FieldReader(), None
        while True:
            job = job if job else self.jobs.get()
            job = self._follow(reader, job) if job else None

    def _follow(self, reader, pasted):
        best, nxt = [], None
        try:
            before = reader.read_focused()
            if before is None or pasted not in before or len(before) > 200_000:  # huge documents: not worth re-reading
                return None
            deadline = time.monotonic() + self.LIMIT
            while time.monotonic() < deadline:
                try:
                    nxt, stop = self.jobs.get(timeout=self.POLL), True
                except queue.Empty:
                    stop = False
                # Always the field that has focus now: web editors rebuild their input element as you type,
                # and when focus moves elsewhere the text no longer matches, which ends the watch.
                after = reader.read_focused()  # one last look when the next dictation starts
                fixes = fixes_in_field(pasted, before, after) if after is not None else None
                if fixes is None:
                    break
                best = fixes
                if stop:
                    break
        except Exception:
            log.exception("edit watcher")
        for old, new in best:
            self.notes.record(old, new)
        return nxt
