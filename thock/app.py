"""One dictation (Session), the app state (App) and startup (main)."""

import array
import asyncio
import ctypes
import json
import logging
import math
import sys
import threading
import time
from collections import deque

import sounddevice as sd

from .config import (APP_NAME, HOME, HOTKEYS, PREVIEW_FONT_SIZES, PREVIEW_FONTS, SAMPLE_RATE, SOUND_KEYBOARDS,
                     TAP_SECONDS, load_settings, log, save_settings)
from .correction import ChatGPTAuth, Polisher
from .editwatch import EditWatcher
from .learning import Profile, TypoNotes
from .overlay import BARS, run_overlay
from .settings_server import SettingsServer
from .sound import KeyboardSounds, selected_mode
from .speech import transcribe
from .live_input import LiveDictation
from .win32 import foreground_app, capture_target, InlineField, kernel32, run_key_hook, user32

TYPING_HOLD = 0.6  # seconds the typing sound outlasts the last change in recognized text
IDLE_STOP = 10  # seconds without new speech that end a tap-started dictation
WAITING = "입력할 곳을 클릭해 주세요."


class Session:
    def __init__(self, app_state, previous):
        self.app, self.state, self.previous = foreground_app(), app_state, previous
        self.target = capture_target()
        self.loop = asyncio.get_running_loop()
        self.audio = asyncio.Queue()
        self.started = time.perf_counter()
        self.released = None
        self.heard_at = None
        self.live, self.entered = None, False
        self.preview = ""  # last heard text, retained if recognition fails
        self.heard_at_release = ""  # what the field showed when recording stopped
        self.done = self.loop.create_future()
        self.stream = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                        blocksize=SAMPLE_RATE // 20, callback=self._on_audio)
        self.stream.start()
        self.task = asyncio.create_task(self.run())
        self.loop.call_later(IDLE_STOP, self._stop_when_idle)

    def _on_audio(self, indata, frames, when, status):
        chunk = bytes(indata)
        self.loop.call_soon_threadsafe(self.audio.put_nowait, chunk)
        samples = array.array("h", chunk)
        rms = math.sqrt(sum(s * s for s in samples) / max(len(samples), 1))
        # -72 dBFS -> flat, -28 dBFS -> full height (this PC's mic idles near -90 dBFS)
        self.state.levels.append(min(max((20 * math.log10(max(rms, 1) / 32768) + 72) / 44, 0.0), 1.0))

    def enter(self):
        """Enter sent the dictated message: write nothing more, not even a pending correction."""
        self.entered = True
        if self.live:
            self.live.blocked = True

    def _stop_when_idle(self):
        """A tap-started dictation ends like a second tap once speech has stopped for IDLE_STOP."""
        if self.state.recording is not self:
            return
        remaining = IDLE_STOP - (time.perf_counter() - (self.heard_at or self.started))
        if remaining <= 0 and self.state.toggle:
            self.state._stop()
        else:
            self.loop.call_later(remaining if remaining > 0 else IDLE_STOP, self._stop_when_idle)

    @property
    def typing(self):
        """Speech is still arriving as text; the typing sound follows it, not the held key."""
        return self.heard_at is not None and time.perf_counter() - self.heard_at < TYPING_HOLD

    def stop(self):
        if self.released is not None:
            return
        self.released = time.perf_counter()
        self.heard_at_release = self.preview
        self.stream.stop()
        self.stream.close()
        self.loop.call_soon(self.audio.put_nowait, None)  # queued after the last audio callbacks

    async def chunks(self):
        while (chunk := await self.audio.get()) is not None:
            yield chunk

    async def run(self):
        s, notes, record = dict(self.state.settings), self.state.notes, {"app": self.app}
        live = None
        try:
            if self.previous:
                await asyncio.shield(self.previous)
            field = await asyncio.to_thread(InlineField, self.target)
            polish = (lambda text: self.state.polisher.polish(text, self.app)) if s["polish"] else None
            live = self.live = LiveDictation(field.update, polish, notes.apply, self.state.show_notice,
                                             field.restart)
            live.waiting = field.stopped
            def heard(text):
                if text.strip() != self.preview:
                    self.heard_at = time.perf_counter()
                    self.loop.call_later(TYPING_HOLD, self.state._sync_sound)
                self.preview = text.strip()
                live.update(text)
                self.state._sync_sound()
            raw = await transcribe(self.chunks(), s["soniox_api_key"],
                                   lambda: self.state.profile.context(self.app, s["terms"] + notes.terms()),
                                   heard, live.endpoint)
            record.update(raw=raw, stt_seconds=round(time.perf_counter() - self.released, 3))
            if not raw.startswith(self.heard_at_release):  # finalizing dropped or changed words
                record["heard_at_release"] = self.heard_at_release
            text = await live.finish(raw)
            record["text"] = text
            if live.error:
                record["polish_error"] = live.error
            if self.entered:
                record["ended"] = "enter"
            elif live.blocked or field.stopped:
                record["error"] = "input_changed"
                record["input_failure"] = field.failure
            elif text and s["learn"]:
                self.state.watcher.watch(text)
            record["total_seconds"] = round(time.perf_counter() - self.released, 3)
        except Exception as error:
            log.warning("dictation failed: %s", type(error).__name__)
            record["error"] = type(error).__name__
            record.setdefault("raw", self.preview)
            if not self.state.notice:
                self.state.show_notice("받아쓰기를 마치지 못했습니다. 입력된 글을 확인해 주세요.")
        finally:
            try:
                self.stop()
                if self.state.recording is self:
                    self.state.recording = None
                if live:
                    await live.close()
                record["recorded_seconds"] = round((self.released or time.perf_counter()) - self.started, 3)
                record["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                with open(HOME / "history.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                if s["learn"]:
                    self.state.profile.maybe_rebuild()
            finally:
                self.state.active.discard(self)
                self.state._sync_sound()
                if not self.done.done():
                    self.done.set_result(None)


class App:
    def __init__(self, settings):
        self.settings = settings
        self.notes = TypoNotes(HOME / "typo_notes.json")
        self.auth = ChatGPTAuth(HOME / "chatgpt_auth.json")
        self.profile = Profile(HOME / "profile.json", HOME / "history.jsonl")
        self.polisher = Polisher(settings, self.notes, self.auth, self.profile)
        # Its own connection and lock: a slow profile build must never hold up a dictation's correction.
        self.profile.complete = Polisher(settings, self.notes, self.auth, self.profile).complete
        self.watcher = EditWatcher(self.notes)
        self.active = set()
        self.recording = None
        self.pressed_at = 0.0
        self.toggle = False
        self.error_until = 0.0
        self.notice = ""
        self.last = None
        self.levels = deque([0.0] * BARS, maxlen=BARS)  # microphone loudness, newest last
        self.devices_changed = False
        self.open_settings = lambda: None  # set once the settings server exists
        self.sounds = KeyboardSounds()
        self.loop = None

    def hotkey_vk(self):
        return HOTKEYS[self.settings["hotkey"]]

    def on_key(self, event):
        """Runs on the asyncio thread. Hold = push-to-talk; short tap = start, next press = stop."""
        now = time.perf_counter()
        if event == "down":
            if self.recording and self.toggle:
                self._stop()
            elif not self.recording:
                if not self.settings["soniox_api_key"]:
                    self.flash_error()
                    self.open_settings()
                    return
                self.notice = ""
                self.pressed_at, self.toggle = now, False
                self.levels.extend([0.0] * BARS)
                self.watcher.flush()  # fixes made to the last paste apply to this dictation
                try:
                    self.recording = self._start_session()
                except Exception:
                    log.exception("microphone failed")
                    self.flash_error()
                    return
                self.active.add(self.recording)
                self.last = self.recording
                self._sync_sound()
        elif event == "enter" and self.recording:
            self.recording.enter()
            self._stop()
        elif event == "up" and self.recording and not self.toggle:
            if now - self.pressed_at < TAP_SECONDS:
                self.toggle = True
            else:
                self._stop()

    def _start_session(self):
        if self.devices_changed and not self.active:
            self._rescan_audio()
        try:
            return Session(self, self.last.done if self.last else None)
        except sd.PortAudioError:
            # The default microphone may have been unplugged or switched; rescan once and retry.
            self._rescan_audio()
            return Session(self, self.last.done if self.last else None)

    def _rescan_audio(self):
        sd._terminate()
        sd._initialize()
        self.devices_changed = False
        log.info("audio devices rescanned")

    def _stop(self):
        self.recording.stop()
        self.recording = None
        self._sync_sound()

    def _sync_sound(self):
        self.sounds.set_mode(selected_mode(self.recording, self.active, self.settings), self.settings["sound_keyboard"])

    def flash_error(self):
        self.error_until = time.perf_counter() + 2

    def status(self):
        """(state, locked) for the overlay; locked means toggle mode is keeping the mic on."""
        if time.perf_counter() < self.error_until:
            return "error", False
        if self.recording:
            return "recording", self.toggle
        if self.active:
            return "processing", False
        return None, False

    def show_notice(self, text):
        self.notice = text
        self.error_until = time.perf_counter() + 8

    def preview(self):
        """The draft lives in the input field; the pill shows a failure (red, briefly) or, for as long
        as the dictation waits for one, that the user should click a text field."""
        if time.perf_counter() < self.error_until:
            return self.notice
        live = getattr(self.last, "live", None)
        return WAITING if live and live.waiting and self.last in self.active else ""

    def public_settings(self):
        s = self.settings
        hint = lambda key: f"••••{key[-4:]}" if key else ""  # noqa: E731
        return {"hotkey": s["hotkey"], "polish": s["polish"], "terms": s["terms"], "learn": s["learn"],
                "sound_recording": s["sound_recording"], "sound_processing": s["sound_processing"],
                "sound_keyboard": s["sound_keyboard"], "sound_keyboards": SOUND_KEYBOARDS,
                "preview": s["preview"], "preview_font_ko": s["preview_font_ko"], "preview_font_en": s["preview_font_en"],
                "preview_font_size": s["preview_font_size"], "preview_fonts": PREVIEW_FONTS,
                "preview_font_sizes": list(PREVIEW_FONT_SIZES),
                "notes": self.notes.listing(), "polish_provider": s["polish_provider"],
                "profile": {**self.profile.data, "building": self.profile.building},
                "chatgpt": {"signed_in": bool(self.auth.tokens), "email": self.auth.email(), **self.auth.login},
                "soniox_key": hint(s["soniox_api_key"]), "openrouter_key": hint(s["openrouter_api_key"])}

    def update_settings(self, body):
        s = self.settings
        if body.get("hotkey") in HOTKEYS:
            s["hotkey"] = body["hotkey"]
        if body.get("polish_provider") in ("chatgpt", "openrouter"):
            s["polish_provider"] = body["polish_provider"]
        if isinstance(body.get("sound_keyboard"), str) and body["sound_keyboard"] in SOUND_KEYBOARDS:
            s["sound_keyboard"] = body["sound_keyboard"]
        for flag in ("polish", "learn", "sound_recording", "sound_processing", "preview"):
            if isinstance(body.get(flag), bool):
                s[flag] = body[flag]
        for lang, fonts in PREVIEW_FONTS.items():
            if isinstance(body.get(f"preview_font_{lang}"), str) and body[f"preview_font_{lang}"] in fonts:
                s[f"preview_font_{lang}"] = body[f"preview_font_{lang}"]
        if body.get("preview_font_size") in PREVIEW_FONT_SIZES:
            s["preview_font_size"] = body["preview_font_size"]
        if isinstance(body.get("terms"), list):
            s["terms"] = [t.strip() for t in body["terms"] if isinstance(t, str) and t.strip()][:500]
        for key in ("soniox_api_key", "openrouter_api_key"):
            if isinstance(body.get(key), str) and body[key].strip():
                s[key] = body[key].strip()
        if "position" in body and body["position"] is None:
            s["position"] = None
        save_settings(s)
        if self.loop:
            self.loop.call_soon_threadsafe(self._sync_sound)
        return self.public_settings()


def main():
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2: crisp overlay on scaled displays
    HOME.mkdir(exist_ok=True)
    logging.basicConfig(filename=HOME / "voicetype.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    kernel32.CreateMutexW(None, False, "Local\\VoiceTypeSingleton")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(f"{APP_NAME} is already running")
    app = App(load_settings())
    app.open_settings = SettingsServer(app).open
    loop = asyncio.new_event_loop()
    app.loop = loop
    threading.Thread(target=loop.run_forever, daemon=True).start()
    threading.Thread(target=run_key_hook, args=(app.hotkey_vk, lambda e: loop.call_soon_threadsafe(app.on_key, e)),
                     daemon=True).start()
    log.info("started, hotkey=%s", app.settings["hotkey"])
    s = app.settings
    polish_ready = app.auth.tokens if s["polish_provider"] == "chatgpt" else s["openrouter_api_key"]
    if not s["soniox_api_key"] or not polish_ready:
        app.open_settings()  # first run: nothing works well until recognition and correction are connected
    try:
        run_overlay(app)
    finally:
        app.sounds.set_mode(None)
