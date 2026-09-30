"""Status overlay: Win32 layered window drawn with GDI+."""

import ctypes
import ctypes.wintypes as wt
import math
import re
import time
from pathlib import Path

from .config import APP_NAME, log, save_settings
from .win32 import LRESULT, WM_TIMER, kernel32, user32


gdiplus = ctypes.WinDLL("gdiplus")
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", wt.BOOL), ("SuppressExternalCodecs", wt.BOOL)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class RectF(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float), ("w", ctypes.c_float), ("h", ctypes.c_float)]


_P, _F, _I = ctypes.c_void_p, ctypes.c_float, ctypes.c_int
_R = ctypes.POINTER(RectF)
for _name, _args in {
    "GdiplusStartup": [ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(GdiplusStartupInput), _P],
    "GdipCreateBitmapFromScan0": [_I, _I, _I, _I, _P, ctypes.POINTER(_P)],
    "GdipGetImageGraphicsContext": [_P, ctypes.POINTER(_P)],
    "GdipSetSmoothingMode": [_P, _I],
    "GdipGraphicsClear": [_P, ctypes.c_uint32],
    "GdipFlush": [_P, _I],
    "GdipCreatePath": [_I, ctypes.POINTER(_P)],
    "GdipAddPathArc": [_P, _F, _F, _F, _F, _F, _F],
    "GdipClosePathFigure": [_P],
    "GdipDeletePath": [_P],
    "GdipCreateSolidFill": [ctypes.c_uint32, ctypes.POINTER(_P)],
    "GdipFillPath": [_P, _P, _P],
    "GdipFillEllipse": [_P, _P, _F, _F, _F, _F],
    "GdipDeleteBrush": [_P],
    "GdipTranslateWorldTransform": [_P, _F, _F, _I],
    "GdipRotateWorldTransform": [_P, _F, _I],
    "GdipResetWorldTransform": [_P],
    "GdipSetTextRenderingHint": [_P, _I],
    "GdipCreateFontFamilyFromName": [wt.LPCWSTR, _P, ctypes.POINTER(_P)],
    "GdipCreateFont": [_P, _F, _I, _I, ctypes.POINTER(_P)],
    "GdipStringFormatGetGenericTypographic": [ctypes.POINTER(_P)],
    "GdipMeasureString": [_P, wt.LPCWSTR, _I, _P, _R, _P, _R, ctypes.POINTER(_I), ctypes.POINTER(_I)],
    "GdipDrawString": [_P, wt.LPCWSTR, _I, _P, _R, _P, _P],
    "GdipDeleteFont": [_P],
    "GdipDeleteFontFamily": [_P],
    "GdipNewPrivateFontCollection": [ctypes.POINTER(_P)],
    "GdipPrivateAddFontFile": [_P, wt.LPCWSTR],
}.items():
    getattr(gdiplus, _name).argtypes = _args
gdiplus.GdiplusStartup(ctypes.byref(ctypes.c_size_t()), ctypes.byref(GdiplusStartupInput(1)), None)
# The preview fonts ship with Thock (thock/fonts, see SOURCE.txt) and are loaded for this process only.
FONTS = _P()
gdiplus.GdipNewPrivateFontCollection(ctypes.byref(FONTS))
for _file in sorted((Path(__file__).resolve().parent / "fonts").glob("*.ttf")):
    gdiplus.GdipPrivateAddFontFile(FONTS, str(_file))
HANGUL = re.compile("[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, _P, wt.UINT, ctypes.POINTER(_P), wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, _I, _I, _I, _I,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, _P]
user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, _P]
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                       ctypes.POINTER(wt.POINT), wt.DWORD, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.ShowWindow.argtypes = [wt.HWND, _I]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.SetProcessDpiAwarenessContext.argtypes = [_P]
user32.SetCapture.argtypes = [wt.HWND]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, _I, _I, _I, wt.HWND, _P]
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
user32.MonitorFromPoint.restype = wt.HMONITOR
user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, _P, wt.UINT]
user32.LoadCursorW.argtypes = [wt.HINSTANCE, _P]
user32.LoadCursorW.restype = wt.HANDLE

BARS = 18  # waveform bars; the microphone delivers one loudness value per 50 ms
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONUP, WM_DEVICECHANGE = 0x200, 0x201, 0x202, 0x205, 0x219


def _capsule(g, x, y, w, h, argb, r=None):
    """Fill an anti-aliased rounded rectangle with corner radius r; by default a pill (rounded ends on the short side)."""
    d = 2 * r if r else min(w, h)
    path, brush = _P(), _P()
    gdiplus.GdipCreatePath(0, ctypes.byref(path))
    gdiplus.GdipAddPathArc(path, x, y, d, d, 180, 90)
    gdiplus.GdipAddPathArc(path, x + w - d, y, d, d, 270, 90)
    gdiplus.GdipAddPathArc(path, x + w - d, y + h - d, d, d, 0, 90)
    gdiplus.GdipAddPathArc(path, x, y + h - d, d, d, 90, 90)
    gdiplus.GdipClosePathFigure(path)
    gdiplus.GdipCreateSolidFill(argb, ctypes.byref(brush))
    gdiplus.GdipFillPath(g, brush, path)
    gdiplus.GdipDeleteBrush(brush)
    gdiplus.GdipDeletePath(path)


def _circle(g, cx, cy, r, argb):
    brush = _P()
    gdiplus.GdipCreateSolidFill(argb, ctypes.byref(brush))
    gdiplus.GdipFillEllipse(g, brush, cx - r, cy - r, 2 * r, 2 * r)
    gdiplus.GdipDeleteBrush(brush)


class Overlay:
    """A small handle above the taskbar that grows into the pill: live waveform while listening,
    ripple while processing, red on error. While a dictation is on its way, a box above the pill shows
    what has been heard so far. Hover shows a settings button above it, right-click shows a menu,
    dragging moves it. It never takes keyboard focus away from the text being written."""
    FULL_W, FULL_H = 132, 36   # pill while listening or hovered, in 96-dpi pixels before SIZE
    IDLE_W, IDLE_H = 44, 10    # resting handle
    GEAR, GAP, MARGIN = 30, 8, 10
    TEXT_W, TEXT_H, PAD_X, PAD_Y, LINES = 600, 96, 20, 11, 2  # preview box: widest, tallest (largest font), padding, lines
    SIZE = 0.8
    MENU_SETTINGS, MENU_RESET, MENU_QUIT = 1, 2, 3

    def __init__(self, app):
        self.app, self.state, self.locked, self.alpha = app, None, False, 0
        self.w, self.h = float(self.IDLE_W), float(self.IDLE_H)
        self.hover, self.press, self.drag_anchor, self.drawn = False, None, None, None
        self.heard, self.preview = None, None  # last text measured, and (text, width, height) to show
        self.dpi = user32.GetDpiForSystem() / 96
        self.s = self.dpi * self.SIZE
        self.bw = round((max(self.FULL_W, self.TEXT_W) + 2 * self.MARGIN) * self.s)
        self.bh = round((self.FULL_H + self.GAP + max(self.GEAR, self.TEXT_H) + 2 * self.MARGIN) * self.s)
        # One premultiplied BGRA surface: GDI+ draws into it, UpdateLayeredWindow shows it.
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), self.bw, -self.bh, 1, 32)
        self.bits = _P()
        self.dc = gdi32.CreateCompatibleDC(None)
        gdi32.SelectObject(self.dc, gdi32.CreateDIBSection(self.dc, ctypes.byref(header), 0, ctypes.byref(self.bits), None, 0))
        bitmap, self.g = _P(), _P()
        gdiplus.GdipCreateBitmapFromScan0(self.bw, self.bh, self.bw * 4, 0xE200B, self.bits, ctypes.byref(bitmap))  # 32bppPARGB
        gdiplus.GdipGetImageGraphicsContext(bitmap, ctypes.byref(self.g))
        gdiplus.GdipSetSmoothingMode(self.g, 4)  # anti-alias
        gdiplus.GdipSetTextRenderingHint(self.g, 5)  # ClearType: the text always sits on the opaque preview box
        self.font, self.fonts, self.font_key, self.format = None, {}, None, _P()
        gdiplus.GdipStringFormatGetGenericTypographic(ctypes.byref(self.format))  # the default adds loose letter spacing
        self.wndproc = WNDPROC(self._wndproc)
        wc = WNDCLASSW(lpfnWndProc=self.wndproc, hInstance=kernel32.GetModuleHandleW(None),
                       hCursor=user32.LoadCursorW(None, _P(32649)),  # hand
                       lpszClassName="ThockOverlay")
        user32.RegisterClassW(ctypes.byref(wc))
        # layered | topmost | tool window (no taskbar button) | no-activate ; WS_POPUP.
        # Fully transparent pixels still let clicks through, so only the pill and gear catch the mouse.
        self.hwnd = user32.CreateWindowExW(0x80000 | 0x8 | 0x80 | 0x08000000, "ThockOverlay", APP_NAME,
                                           0x80000000, 0, 0, self.bw, self.bh, None, None, wc.hInstance, None)
        user32.SetTimer(self.hwnd, 1, 16, None)

    # --- geometry (physical screen pixels) ---

    def default_anchor(self):
        work = wt.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA: screen minus taskbar
        return (work.left + work.right) // 2, work.bottom - round(4 * self.dpi)

    def anchor(self):
        """Bottom-centre of the pill: where the user dragged it, else centred 4 px above the taskbar."""
        if self.drag_anchor:
            return self.drag_anchor
        pos = self.app.settings.get("position")
        if pos and user32.MonitorFromPoint(wt.POINT(*pos), 0):  # still on a connected screen
            return tuple(pos)
        return self.default_anchor()

    def _pill_rect(self, w, h):
        cx, bottom = self.anchor()
        return cx - w * self.s / 2, bottom - h * self.s, cx + w * self.s / 2, bottom

    def _gear_center(self):
        cx, bottom = self.anchor()
        return cx, bottom - (self.FULL_H + self.GAP + self.GEAR / 2) * self.s

    def _hit(self, x, y):
        """'gear', 'pill' or None for a screen point, using the currently shown shape."""
        if self.hover and not self.state:
            gx, gy = self._gear_center()
            if math.hypot(x - gx, y - gy) <= self.GEAR / 2 * self.s + 2:
                return "gear"
            left, top, right, bottom = self._pill_rect(self.FULL_W, self.FULL_H)
            # keep hovering while crossing the gap between pill and gear
            if left <= x <= right and gy <= y <= bottom:
                return "pill"
            return None
        pad = 6 * self.dpi  # the resting handle is small; give the pointer some room
        left, top, right, bottom = self._pill_rect(self.w, self.h)
        return "pill" if left - pad <= x <= right + pad and top - pad <= y <= bottom + pad else None

    def _cursor(self):
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    # --- input ---

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            return self._handle(hwnd, msg, wparam, lparam)
        except Exception:  # an exception inside a ctypes callback would otherwise vanish without a trace
            log.exception("overlay message 0x%X", msg)
            return 0

    def _handle(self, hwnd, msg, wparam, lparam):
        if msg == WM_TIMER:
            self.frame()
        elif msg == WM_LBUTTONDOWN:
            user32.SetCapture(hwnd)
            x, y = self._cursor()
            self.press = (x, y, self.anchor(), self._hit(x, y))
        elif msg == WM_MOUSEMOVE and self.press:
            x, y = self._cursor()
            px, py, (ax, ay), _ = self.press
            if self.drag_anchor or math.hypot(x - px, y - py) > 4 * self.dpi:
                self.drag_anchor = (ax + x - px, ay + y - py)
        elif msg == WM_LBUTTONUP and self.press:
            user32.ReleaseCapture()
            target, self.press = self.press[3], None
            if self.drag_anchor:
                self.app.settings["position"], self.drag_anchor = list(self.drag_anchor), None
                save_settings(self.app.settings)
            elif target == "gear":
                self.app.open_settings()
        elif msg == WM_RBUTTONUP:
            self._menu()
        elif msg == WM_DEVICECHANGE:
            self.app.devices_changed = True  # a microphone was plugged, unplugged or switched
        else:
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        return 0

    def _menu(self):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, self.MENU_SETTINGS, "설정")
        user32.AppendMenuW(menu, 0, self.MENU_RESET, "위치 초기화")
        user32.AppendMenuW(menu, 0x800, 0, None)  # separator
        user32.AppendMenuW(menu, 0, self.MENU_QUIT, f"{APP_NAME} 종료")
        x, y = self._cursor()
        user32.SetForegroundWindow(self.hwnd)  # lets the menu close when the user clicks elsewhere
        choice = user32.TrackPopupMenu(menu, 0x0100 | 0x0020 | 0x0004, x, y, 0, self.hwnd, None)  # RETURNCMD|BOTTOMALIGN|CENTERALIGN
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        if choice == self.MENU_SETTINGS:
            self.app.open_settings()
        elif choice == self.MENU_RESET:
            self.app.update_settings({"position": None})
        elif choice == self.MENU_QUIT:
            user32.PostQuitMessage(0)

    # --- drawing ---

    def frame(self):
        state, locked = self.app.status()
        self.hover = bool(self.press) or (not state and self._hit(*self._cursor()) is not None)
        if state:
            self.state, self.locked = state, locked
        elif self.w <= self.IDLE_W + 0.5:
            self.state = None  # keep the last look while shrinking back
        big = bool(state) or self.hover
        tw, th = (self.FULL_W, self.FULL_H) if big else (self.IDLE_W, self.IDLE_H)
        self.w += (tw - self.w) * 0.3
        self.h += (th - self.h) * 0.3
        if abs(tw - self.w) < 0.5:
            self.w, self.h = float(tw), float(th)
        target_alpha = 255 if big else 190
        step = 40 if target_alpha > self.alpha else 16
        self.alpha = min(target_alpha, self.alpha + step) if target_alpha > self.alpha else max(target_alpha, self.alpha - step)
        self._update_font()
        self._fit(self.app.preview())

        live = self.state == "recording" and tuple(self.app.levels)
        ripple = self.state == "processing" and int(time.perf_counter() * 60)
        key = (self.state, self.locked, self.hover, self.w, self.h, self.alpha, live, ripple, self.preview, self.anchor())
        if key == self.drawn:
            return  # nothing changed: stay idle, no redraw
        self.drawn = key
        self.draw()
        cx, bottom = self.anchor()
        x, y = cx - self.bw // 2, bottom + round(self.MARGIN * self.s) - self.bh
        user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(wt.POINT(x, y)), ctypes.byref(wt.SIZE(self.bw, self.bh)),
                                   self.dc, ctypes.byref(wt.POINT(0, 0)), 0,
                                   ctypes.byref(BLENDFUNCTION(0, 0, self.alpha, 1)), 2)  # AC_SRC_ALPHA, ULW_ALPHA
        if not user32.IsWindowVisible(self.hwnd):
            user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE

    def draw(self):
        g, s = self.g, self.s
        gdiplus.GdipGraphicsClear(g, 0)
        w, h = self.w * s, self.h * s
        x0, y0 = (self.bw - w) / 2, self.bh - self.MARGIN * s - h
        for i in range(4, 0, -1):  # soft shadow, slightly lower than the pill
            _capsule(g, x0 - i * s, y0 - i * s + 2 * s, w + 2 * i * s, h + 2 * i * s, (0x1C - 5 * i) << 24)
        error = self.state == "error"
        _capsule(g, x0, y0, w, h, 0xFF5A2320 if error else 0xFF3A3A3A)  # hairline edge
        _capsule(g, x0 + s, y0 + s, w - 2 * s, h - 2 * s, 0xFFB3261E if error else 0xFF0F0F0F)

        grown = (self.w - self.IDLE_W) / (self.FULL_W - self.IDLE_W)  # 0 = resting handle, 1 = full pill
        if grown > 0.85:
            locked = self.locked and self.state == "recording"
            n = BARS - 3 if locked else BARS
            bar, gap, tallest = 3 * s, 2.4 * s, 20 * s
            left = x0 + (w - (n * bar + (n - 1) * gap)) / 2 + (7 * s if locked else 0)
            if locked:  # toggle mode: the mic stays on until the next press
                _circle(g, x0 + 16 * s, y0 + h / 2, 3 * s, 0xFFFF453A)
            levels, t = list(self.app.levels)[-n:], time.perf_counter()
            for i in range(n):
                if self.state == "recording":
                    v, color = levels[i], 0xF2FFFFFF
                elif self.state == "processing":
                    v, color = 0.16 + 0.14 * math.sin(t * 7 - i * 0.55), 0x9CFFFFFF
                else:
                    v, color = 0.0, 0x9CFFFFFF
                height = bar + v * (tallest - bar)
                _capsule(g, left + i * (bar + gap), y0 + (h - height) / 2, bar, height, color)

        if self.hover and not self.state and grown > 0.85:
            self._draw_gear(self.bw / 2, y0 - (self.GAP + self.GEAR / 2) * s)
        if self.preview and grown > 0.85:
            self._draw_preview(y0 - self.GAP * s)
        gdiplus.GdipFlush(g, 1)

    def _update_font(self):
        """Make the Korean and English preview fonts again when their settings change;
        Malgun Gothic if a bundled font is missing."""
        s = self.app.settings
        want = (s["preview_font_ko"], s["preview_font_en"], s["preview_font_size"])
        if want == self.font_key:
            return
        self.font_key, self.heard = want, None  # measure the text again in the new font
        for font in self.fonts.values():
            gdiplus.GdipDeleteFont(font)
        for lang, name in zip(("ko", "en"), want):
            family, self.fonts[lang] = _P(), _P()
            if gdiplus.GdipCreateFontFamilyFromName(name, FONTS, ctypes.byref(family)):
                gdiplus.GdipCreateFontFamilyFromName("Malgun Gothic", None, ctypes.byref(family))
            gdiplus.GdipCreateFont(family, want[2] * self.dpi, 0, 2, ctypes.byref(self.fonts[lang]))  # regular, pixels
            gdiplus.GdipDeleteFontFamily(family)

    def _measure(self, text):
        """(width, height, lines) of text wrapped to the preview box, in screen pixels."""
        box, fitted, lines = RectF(), _I(), _I()
        room = RectF(0, 0, (self.TEXT_W - 2 * self.PAD_X) * self.s, 1e5)
        gdiplus.GdipMeasureString(self.g, text, -1, self.font, ctypes.byref(room), self.format,
                                  ctypes.byref(box), ctypes.byref(fitted), ctypes.byref(lines))
        return box.w, box.h, lines.value

    def _fit(self, heard):
        """Keep the end of heard that fits in LINES lines: self.preview = (text, width, height), or None."""
        if heard == self.heard:
            return
        self.heard, lo, hi = heard, 0, len(heard)
        self.font = self.fonts["ko" if HANGUL.search(heard) else "en"]  # English fonts have no Hangul
        while lo < hi:  # the earliest start whose tail still fits
            mid = (lo + hi) // 2
            if self._measure(("…" if mid else "") + heard[mid:])[2] <= self.LINES:
                hi = mid
            else:
                lo = mid + 1
        if lo and (space := heard.find(" ", lo, lo + 8)) > 0:
            lo = space + 1  # start at a word
        text = ("…" if lo else "") + heard[lo:]
        self.preview = (text, *self._measure(text)[:2]) if heard else None

    def _draw_preview(self, bottom):
        """The box above the pill showing the end of what has been heard."""
        g, s, px, py = self.g, self.s, self.PAD_X * self.s, self.PAD_Y * self.s
        text, tw, th = self.preview
        w, h = tw + 2 * px, th + 2 * py
        x, y = (self.bw - w) / 2, bottom - h
        _capsule(g, x, y, w, h, 0xFF3A3A3A, 10 * s)  # hairline edge
        _capsule(g, x + s, y + s, w - 2 * s, h - 2 * s, 0xFF0F0F0F, 9 * s)
        brush = _P()
        gdiplus.GdipCreateSolidFill(0xFFFFFFFF, ctypes.byref(brush))
        room = RectF(x + px, y + py, (self.TEXT_W - 2 * self.PAD_X) * s, th + s)  # same width as measured, same wrapping
        gdiplus.GdipDrawString(g, text, -1, self.font, ctypes.byref(room), self.format, brush)
        gdiplus.GdipDeleteBrush(brush)

    def _draw_gear(self, cx, cy):
        g, s = self.g, self.s
        r = self.GEAR / 2 * s
        _circle(g, cx, cy + 1.5 * s, r + 1.5 * s, 0x22000000)  # shadow
        _circle(g, cx, cy, r, 0xFF3A3A3A)                    # hairline edge
        _circle(g, cx, cy, r - s, 0xFF0F0F0F)
        tooth_w, tooth_h, ring = 2.6 * s, 3.2 * s, 5.2 * s
        for k in range(8):  # teeth around the ring
            gdiplus.GdipRotateWorldTransform(g, k * 45.0, 1)  # rotate about the origin, then move to the centre
            gdiplus.GdipTranslateWorldTransform(g, cx, cy, 1)
            _capsule(g, -tooth_w / 2, -ring - tooth_h + 1.2 * s, tooth_w, tooth_h, 0xFFFFFFFF)
            gdiplus.GdipResetWorldTransform(g)
        _circle(g, cx, cy, ring, 0xFFFFFFFF)
        _circle(g, cx, cy, 2.2 * s, 0xFF0F0F0F)


def run_overlay(app):
    overlay = Overlay(app)  # noqa: F841  (kept alive for its window procedure)
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
