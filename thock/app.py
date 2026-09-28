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

from .config import APP_NAME, HOME, HOTKEYS, SAMPLE_RATE, TAP_SECONDS, load_settings, log, save_settings
from .correction import ChatGPTAuth, Polisher
from .editwatch import EditWatcher
from .learning import Profile, TypoNotes
from .overlay import BARS, run_overlay
from .settings_server import SettingsServer
from .sound import KeyboardSounds, selected_mode
from .speech import transcribe
from .win32 import foreground_app, kernel32, paste, run_key_hook, user32


class Session:
    def __init__(self, app_state, previous):
        self.app, self.state, self.previous = foreground_app(), app_state, previous
        self.loop = asyncio.get_running_loop()
        self.audio = asyncio.Queue()
        self.started = time.perf_counter()
        self.released = None
        self.preview = ""  # what has been heard so far, shown above the pill until the paste
        self.done = self.loop.create_future()
        self.stream = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                        blocksize=SAMPLE_RATE // 20, callback=self._on_audio)
        self.stream.start()
        self.task = asyncio.create_task(self.run())

    def _on_audio(self, indata, frames, when, status):
        chunk = bytes(indata)
        self.loop.call_soon_threadsafe(self.audio.put_nowait, chunk)
        samples = array.array("h", chunk)
        rms = math.sqrt(sum(s * s for s in samples) / max(len(samples), 1))
        # -72 dBFS -> flat, -28 dBFS -> full height (this PC's mic idles near -90 dBFS)
        self.state.levels.append(min(max((20 * math.log10(max(rms, 1) / 32768) + 72) / 44, 0.0), 1.0))

    def stop(self):
        self.released = time.perf_counter()
        self.stream.stop()
        self.stream.close()
        self.loop.call_soon(self.audio.put_nowait, None)  # queued after the last audio callbacks

    async def chunks(self):
        while (chunk := await self.audio.get()) is not None:
            yield chunk

    async def run(self):
        s, notes, record = self.state.settings, self.state.notes, {"app": self.app}
        try:
            raw = await transcribe(self.chunks(), s["soniox_api_key"],
                                   lambda: self.state.profile.context(self.app, s["terms"] + notes.terms()),
                                   lambda heard: setattr(self, "preview", heard.strip()))
            record.update(raw=raw, stt_seconds=round(time.perf_counter() - self.released, 3))
            text = raw
            if raw and s["polish"]:
                try:
                    text = await asyncio.to_thread(self.state.polisher.polish, raw, self.app)
                except Exception as e:
                    log.warning("polish skipped: %s", e)
                    record["polish_error"] = str(e)
            text = notes.apply(text)
            record["text"] = text
            if self.previous:
                await self.previous  # keep pastes in the order they were spoken
            if text:
                await asyncio.to_thread(paste, text)
                if s["learn"]:
                    self.state.watcher.watch(text)
            record["total_seconds"] = round(time.perf_counter() - self.released, 3)
        except Exception as e:
            log.exception("dictation failed")
            record["error"] = str(e)
            self.state.flash_error()
        finally:
            try:
                record["recorded_seconds"] = round((self.released or time.perf_counter()) - self.started, 3)
                record["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
                with open(HOME / "history.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                if s["learn"]:
                    self.state.profile.maybe_rebuild()
            finally:
                self.state.active.discard(self)
                self.state._sync_sound()
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
        self.sounds.set_mode(selected_mode(self.recording, self.active, self.settings))

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

    def preview(self):
        """For the overlay: what the newest dictation has heard so far, until it is pasted."""
        session = self.last
        return session.preview if session and session in self.active else ""

    def public_settings(self):
        s = self.settings
        hint = lambda key: f"••••{key[-4:]}" if key else ""  # noqa: E731
        return {"hotkey": s["hotkey"], "polish": s["polish"], "terms": s["terms"], "learn": s["learn"],
                "sound_recording": s["sound_recording"], "sound_processing": s["sound_processing"],
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
        for flag in ("polish", "learn", "sound_recording", "sound_processing"):
            if isinstance(body.get(flag), bool):
                s[flag] = body[flag]
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
