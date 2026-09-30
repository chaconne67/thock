"""After a paste, re-read the text field through UI Automation and record the words the user fixed."""

import ctypes
from contextlib import contextmanager
import queue
import threading
import time

from .config import log
from .learning import fixes_in_field


ole32, oleaut32 = ctypes.WinDLL("ole32"), ctypes.WinDLL("oleaut32")
oleaut32.SysStringLen.argtypes = [ctypes.c_void_p]
oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
oleaut32.SafeArrayGetLBound.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_long)]
oleaut32.SafeArrayGetUBound.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_long)]
oleaut32.SafeArrayGetElement.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long), ctypes.c_void_p]
oleaut32.SafeArrayDestroy.argtypes = [ctypes.c_void_p]
_local_reader = threading.local()


def field_reader():
    if not hasattr(_local_reader, "reader"):
        _local_reader.reader = FieldReader()
    return _local_reader.reader


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



def _ok(hr):
    if hr < 0:
        raise RuntimeError(f"text range unavailable: HRESULT {hr & 0xffffffff:08x}")


def _text(rng):
    value = ctypes.c_void_p()
    _ok(_com(rng, 12, ctypes.c_int, _PP)(rng, 200001, ctypes.byref(value)))
    text = _bstr(value.value)
    if len(text) > 200000:
        raise RuntimeError("text range too large")
    return text


def _clone(rng):
    result = ctypes.c_void_p()
    _ok(_com(rng, 3, _PP)(rng, ctypes.byref(result)))
    return result


def _endpoint(rng, end, other, other_end):
    _ok(_com(rng, 15, ctypes.c_int, ctypes.c_void_p, ctypes.c_int)(rng, end, other, other_end))


def _snapshot(document, selected):
    before, after = _clone(document), None
    try:
        after = _clone(document)
        _endpoint(before, 1, selected, 0)
        _endpoint(after, 0, selected, 1)
        result = (_text(before), _text(selected), _text(after))
        if "".join(result) != _text(document):
            raise RuntimeError("inconsistent text range")
        return result
    finally:
        _release(before)
        _release(after)


_native_user32 = ctypes.WinDLL("user32", use_last_error=True)
_native_user32.SendMessageTimeoutW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                              ctypes.c_ssize_t, ctypes.c_uint, ctypes.c_uint,
                                              ctypes.POINTER(ctypes.c_size_t)]
_native_user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
_native_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_native_user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]


def _message(window, message, wparam=0, lparam=0):
    result = ctypes.c_size_t()
    if not _native_user32.SendMessageTimeoutW(window, message, wparam, lparam, 2, 250, ctypes.byref(result)):
        raise RuntimeError("native editor unavailable")
    return result.value


class FieldReader:
    """Reads the focused text field of any app through UI Automation (COM, one thread only)."""
    IID_VALUE, IID_TEXT = _guid("{a94cd8b1-0844-4cd6-9d2d-640537ab39e9}"), _guid("{32eba289-3583-42c9-9c59-3b6d9a1e9b6a}")

    def __init__(self):
        ole32.CoInitializeEx(None, 0)
        self.uia = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(_guid("{ff48dba4-60ef-4201-aa87-54103eef594e}")), None, 1,
                               ctypes.byref(_guid("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")), ctypes.byref(self.uia))

    def focus_id(self):
        element, rid = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            if not self.uia:
                return None
            if _com(self.uia, 8, _PP)(self.uia, ctypes.byref(element)) < 0 or not element:
                return None
            if _com(element, 4, _PP)(element, ctypes.byref(rid)) < 0 or not rid:
                return None
            lo, hi = ctypes.c_long(), ctypes.c_long()
            if (oleaut32.SafeArrayGetLBound(rid, 1, ctypes.byref(lo)) < 0
                    or oleaut32.SafeArrayGetUBound(rid, 1, ctypes.byref(hi)) < 0
                    or not 0 <= hi.value - lo.value < 64):
                return None
            values = []
            for i in range(lo.value, hi.value + 1):
                index, value = ctypes.c_long(i), ctypes.c_long()
                if oleaut32.SafeArrayGetElement(rid, ctypes.byref(index), ctypes.byref(value)) < 0:
                    return None
                values.append(value.value)
            return tuple(values)
        except OSError:
            return None
        finally:
            if rid:
                oleaut32.SafeArrayDestroy(rid)
            _release(element)

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


    @contextmanager
    def selection(self):
        """Fresh document and single selection; no COM pointer escapes this thread."""
        element, pattern, document, ranges, selected = [ctypes.c_void_p() for _ in range(5)]
        try:
            _ok(_com(self.uia, 8, _PP)(self.uia, ctypes.byref(element)))
            password = ctypes.c_int()
            _ok(_com(element, 35, ctypes.POINTER(ctypes.c_int))(element, ctypes.byref(password)))
            if password.value:
                raise RuntimeError("password field")
            _ok(_com(element, 14, ctypes.c_int, ctypes.POINTER(GUID), _PP)(
                element, 10014, ctypes.byref(self.IID_TEXT), ctypes.byref(pattern)))
            if not pattern:
                raise RuntimeError("text ranges unavailable")
            _ok(_com(pattern, 7, _PP)(pattern, ctypes.byref(document)))
            _ok(_com(pattern, 5, _PP)(pattern, ctypes.byref(ranges)))
            count = ctypes.c_int()
            _ok(_com(ranges, 3, ctypes.POINTER(ctypes.c_int))(ranges, ctypes.byref(count)))
            if count.value != 1:
                raise RuntimeError("single caret required")
            _ok(_com(ranges, 4, ctypes.c_int, _PP)(ranges, 0, ctypes.byref(selected)))
            yield document, selected
        finally:
            for obj in (selected, ranges, document, pattern, element):
                _release(obj)

    def snapshot(self):
        """Text before the selection, the selection, and text after it (including offscreen text)."""
        try:
            with self.selection() as (document, selected):
                return _snapshot(document, selected)
        except (OSError, RuntimeError):
            native = self.native_selection()
            return native[1] if native else None

    def native_selection(self):
        """Classic Edit controls expose caret offsets through their native Windows contract."""
        element = ctypes.c_void_p()
        try:
            _ok(_com(self.uia, 8, _PP)(self.uia, ctypes.byref(element)))
            window = ctypes.c_void_p()
            _ok(_com(element, 36, _PP)(element, ctypes.byref(window)))
            if not window:
                return None
            name = ctypes.create_unicode_buffer(128)
            _native_user32.GetClassNameW(window, name, len(name))
            if name.value.lower() != "edit" and not name.value.lower().startswith("richedit"):
                return None
            if _native_user32.GetWindowLongW(window, -16) & (0x20 | 0x800):  # ES_PASSWORD / ES_READONLY
                return None
            size = _message(window, 0x000E)  # WM_GETTEXTLENGTH
            if size > 200000:
                return None
            buffer = ctypes.create_unicode_buffer(size + 1)
            _message(window, 0x000D, size + 1, ctypes.addressof(buffer))  # WM_GETTEXT
            start, end = ctypes.c_ulong(), ctypes.c_ulong()
            _message(window, 0x00B0, ctypes.addressof(start), ctypes.addressof(end))  # EM_GETSEL
            encoded = buffer.value.encode("utf-16-le")
            if not 0 <= start.value <= end.value <= len(encoded) // 2:
                return None
            parts = (encoded[:start.value * 2], encoded[start.value * 2:end.value * 2], encoded[end.value * 2:])
            return window.value, tuple(part.decode("utf-16-le") for part in parts), start.value, end.value
        except (OSError, RuntimeError, UnicodeError):
            return None
        finally:
            _release(element)

    def select_tail(self, expected, tail):
        """Select only the exact suffix immediately before the observed caret."""
        try:
            with self.selection() as (document, selected):
                if _snapshot(document, selected) != expected:
                    return None
                if not tail:
                    return expected
                before, found = _clone(document), ctypes.c_void_p()
                try:
                    _endpoint(before, 1, selected, 0)
                    oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
                    oleaut32.SysAllocString.restype = ctypes.c_void_p
                    needle = oleaut32.SysAllocString(tail)
                    try:
                        _ok(_com(before, 8, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, _PP)(
                            before, needle, True, False, ctypes.byref(found)))
                    finally:
                        oleaut32.SysFreeString(needle)
                    if not found:
                        return None
                    comparison = ctypes.c_int()
                    _ok(_com(found, 5, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                             ctypes.POINTER(ctypes.c_int))(found, 1, selected, 0, ctypes.byref(comparison)))
                    wanted = (expected[0][:-len(tail)], tail, expected[2])
                    if comparison.value or _snapshot(document, found) != wanted:
                        return None
                    _ok(_com(found, 16)(found))
                    return wanted
                finally:
                    _release(found)
                    _release(before)
        except (OSError, RuntimeError):
            native = self.native_selection()
            if not native or native[1] != expected or expected[1] or not expected[0].endswith(tail):
                return None
            try:
                _message(native[0], 0x00B1, native[2] - len(tail.encode("utf-16-le")) // 2, native[3])
                return (expected[0][:-len(tail)], tail, expected[2]) if tail else expected
            except (OSError, RuntimeError):
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
