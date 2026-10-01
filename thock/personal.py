"""Account-local data protected by Windows DPAPI; no supplier keys or audio.

Learning history keeps at most 1,000 entries from the past 30 days.
Recovery text is separately bounded to the last five items until copied/deleted.
Legacy files are read only on explicit import and are never overwritten.
"""
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import threading
import time
import wave
from pathlib import Path

from .config import SAMPLE_RATE

MAGIC = b"THOCK1\0"
_history_lock = threading.RLock()


class Blob(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(data, decrypt=False):
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wt.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), "Thock", None, None, None, 1, ctypes.byref(result))
    fn.restype = wt.BOOL
    if not fn(*args):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def read_bytes(path):
    data = path.read_bytes()
    if data.startswith(MAGIC):
        return _protect(data[len(MAGIC):], decrypt=True)
    if path.suffix == ".protected":
        raise ValueError("protected data has an invalid header")
    return data


def read_data(path, default=None):
    if not path.exists():
        return default
    return json.loads(read_bytes(path).decode("utf-8"))


def write_data(path, value):
    write_bytes(path, json.dumps(value, ensure_ascii=False).encode("utf-8"))


AUDIO_KEPT = 30


def keep_audio(folder, name, pcm):
    """A dictation's audio exactly as sent to Soniox, protected for this Windows user, for diagnosis only.
    Never sent anywhere; the newest AUDIO_KEPT are kept."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    write_bytes(folder / f"{name}.wav.protected", buffer.getvalue())
    for old in sorted(folder.glob("*.wav.protected"))[:-AUDIO_KEPT]:
        old.unlink()


def write_bytes(path, data):
    if path.suffix == ".protected":
        encrypted = _protect(data)
        if _protect(encrypted, decrypt=True) != data:
            raise OSError("protected data could not be verified")
        data = MAGIC + encrypted
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def history_data(path):
    if path.suffix == ".protected":
        with _history_lock:
            data = read_data(path, {"total": 0, "rows": []})
            cutoff = time.time() - 30 * 86400
            rows = [row for row in data["rows"] if row.get("saved_at", 0) > cutoff][-1000:]
            if rows != data["rows"]:
                data["rows"] = rows
                write_data(path, data)
            return data
    if not path.exists():
        return {"total": 0, "rows": []}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"total": len([r for r in rows if r.get("text")]), "rows": rows}


def append_history(path, record):
    with _history_lock:
        data = history_data(path)
        now = time.time()
        data["rows"] = [row for row in data["rows"]
                        if row.get("saved_at", now) > now - 30 * 86400][-999:]
        data["rows"].append({**record, "saved_at": now})
        data["total"] += 1
        write_data(path, data)


def import_legacy(home, root):
    """Explicit first-account import. Verify encrypted copies before returning."""
    for old, new in (("typo_notes.json", "notes.protected"), ("profile.json", "profile.protected")):
        if (home / old).exists() and not (root / new).exists():
            write_data(root / new, read_data(home / old))
    if (home / "history.jsonl").exists() and not (root / "history.protected").exists():
        data = history_data(home / "history.jsonl")
        # A legacy import is a user-approved copy; retention starts at import.
        rows = [{**row, "saved_at": time.time()} for row in data["rows"][-1000:] if row.get("text")]
        write_data(root / "history.protected", {"total": data["total"], "rows": rows})
    config = read_data(home / "settings.json", {})
    if not (root / "terms.protected").exists():
        write_data(root / "terms.protected", config.get("terms", []))
