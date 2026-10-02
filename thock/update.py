"""Updating itself (주인님 결정 2026-10-02): a Thock newer than this one, as the server offers it for download, is
fetched in the background and checked against the server's SHA-256, then the usual installer runs silently once
dictation has rested for five minutes; it stops Thock, replaces it and starts it again. Not for a Thock run from
source, the one built into Crema (Crema updates it) or one installed from the Store (the Store does)."""
import ctypes
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from .account import SITE, Account
from .config import EMBEDDED, HOME, VERSION

CHECK_SECONDS = 6 * 3600
IDLE_SECONDS = 300
FOLDER = Path(tempfile.gettempdir()) / "ThockUpdate"
MARK = HOME / "updated.json"  # the version an update went to, for the message after it


def enabled():
    if EMBEDDED or not getattr(sys, "frozen", False):
        return False
    length = ctypes.c_uint32(0)  # APPMODEL_ERROR_NO_PACKAGE (15700): installed by the installer, not the Store
    return ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None) == 15700


def _order(version):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.dev(\d+))?", version or "")
    if not match:
        return None
    major, minor, patch, dev = match.groups()
    return int(major), int(minor), int(patch), int(dev) if dev else float("inf")


def newer(offered, current=VERSION):
    """Only ever forward: a development build is not replaced by an older release."""
    offered, current = _order(offered), _order(current)
    return offered is not None and current is not None and offered > current


def fetch():
    """The installer of a newer Thock, downloaded and matching its SHA-256: (version, path), or None."""
    latest = Account._request("/api/thock/latest", timeout=15)
    version, url, digest = latest.get("version"), latest.get("url"), str(latest.get("sha256") or "").lower()
    if not newer(version) or not isinstance(url, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    FOLDER.mkdir(exist_ok=True)
    path = FOLDER / f"Thock-setup-{version}.exe"
    if not (path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest):
        partial = path.with_suffix(".part")
        request = urllib.request.Request(urllib.parse.urljoin(SITE, url), headers={"User-Agent": f"Thock/{VERSION}"})
        with urllib.request.urlopen(request, timeout=120) as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out)
        if hashlib.sha256(partial.read_bytes()).hexdigest() != digest:
            partial.unlink(missing_ok=True)
            raise ValueError("the downloaded installer does not match its SHA-256")
        partial.replace(path)
    return version, path


def install(version, path):
    """Run the installer apart from Thock: through cmd's start it is not Thock's child, and so survives the
    installer stopping Thock's process tree."""
    MARK.write_text(json.dumps({"to": version}), encoding="utf-8")
    subprocess.Popen(["cmd.exe", "/c", "start", "", str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                     creationflags=subprocess.CREATE_NO_WINDOW)


def updated():
    """Once after an update: this version, when the update went to it; the downloaded installers are cleared."""
    try:
        mark = json.loads(MARK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    MARK.unlink(missing_ok=True)
    shutil.rmtree(FOLDER, ignore_errors=True)
    return VERSION if mark.get("to") == VERSION else None
