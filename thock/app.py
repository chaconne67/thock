"""One dictation (Session), the app state (App) and startup (main)."""

import array
import asyncio
import ctypes
import json
import hashlib
import uuid
import logging
import math
import platform
import sys
import threading
import time
import traceback
from collections import deque

import sounddevice as sd

from .config import (APP_NAME, HOME, HOTKEYS, INPUT_MODES, PREVIEW_FONT_SIZES, PREVIEW_FONTS, SAMPLE_RATE, SOUND_KEYBOARDS,
                     TAP_SECONDS, VERSION, load_settings, log, save_settings)
from .account import Account, AccountError
from .correction import PROFILE_PROMPT, Polisher
from .editwatch import EditWatcher
from .learning import Profile, TypoNotes
from .overlay import BARS, run_overlay
from .settings_server import SettingsServer
from .sound import KeyboardSounds, selected_mode
from .speech import transcribe
from .live_input import LiveDictation
from .win32 import foreground_app, capture_target, copy_text, InlineField, kernel32, run_key_hook, user32
from .personal import append_history, read_data, write_data, import_legacy, history_data

TYPING_HOLD = 0.6  # seconds the typing sound outlasts the last change in recognized text
IDLE_STOP = 10  # seconds without new speech that end a tap-started dictation
WAITING = "입력할 곳을 클릭해 주세요."
VOICE_LEVEL = 0.6  # microphone level (about -46 dBFS) taken as the start of speech, for diagnosis only


class Session:
    def __init__(self, app_state, previous):
        self.app, self.state, self.previous = foreground_app(), app_state, previous
        self.target = capture_target()
        self.settings = dict(app_state.settings)
        self.notes, self.profile = app_state.notes, app_state.profile
        self.loop = asyncio.get_running_loop()
        self.audio = asyncio.Queue()
        self.started = time.perf_counter()
        self.released = None
        self.preview = ""  # last heard text, kept for recovery if dictation fails
        self.heard_at = None
        self.heard_at_release = ""  # what the field showed when recording stopped
        # First-words diagnosis: a waking microphone, a quiet start, or recognition that came late.
        self.idle_s = round(self.started - app_state.idle_since)
        self.first_audio = self.voice_at = self.first_text_at = None
        self.record = None  # this dictation's record once it has finished
        self.live, self.entered = None, False
        self.overflows = 0  # microphone buffer overruns, for error reports
        self.notices = []  # every red message shown while this dictation was active
        self.sent_frames = 0
        self.captured_frames = 0
        self.max_frames = 300 * SAMPLE_RATE
        self.session_id = None
        self.finished = False
        self.cleanup_failed = False
        self.done = self.loop.create_future()
        self.stream = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                        blocksize=SAMPLE_RATE // 20, callback=self._on_audio)
        try:
            self.stream.start()
        except BaseException:
            self.stream.close()
            raise
        self.limit_timer = self.loop.call_later(300, self.stop)
        self.task = asyncio.create_task(self.run())
        self.loop.call_later(IDLE_STOP, self._stop_when_idle)

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

    def _on_audio(self, indata, frames, when, status):
        if self.released is not None:
            return
        if status.input_overflow:
            self.overflows += 1
        now = time.perf_counter()
        if self.first_audio is None:
            self.first_audio = now
        chunk = bytes(indata)
        self.loop.call_soon_threadsafe(self._enqueue, chunk)
        samples = array.array("h", chunk)
        rms = math.sqrt(sum(s * s for s in samples) / max(len(samples), 1))
        level = min(max((20 * math.log10(max(rms, 1) / 32768) + 72) / 44, 0.0), 1.0)
        if self.voice_at is None and level >= VOICE_LEVEL:
            self.voice_at = now
        self.state.levels.append(level)

    def timing(self):
        """Idle time before this dictation and when sound, voice and the first words arrived."""
        marks = {"first_audio_ms": self.first_audio, "silent_start_ms": self.voice_at,
                 "first_text_ms": self.first_text_at}
        return {"idle_s": self.idle_s, **{name: round((at - self.started) * 1000)
                                          for name, at in marks.items() if at is not None}}

    def _enqueue(self, chunk):
        if self.finished:
            return
        remaining = self.max_frames - self.captured_frames
        if remaining > 0:
            chunk = chunk[:remaining * 2]
            self.captured_frames += len(chunk) // 2
            self.audio.put_nowait(chunk)
        if self.captured_frames >= self.max_frames:
            self.stop()

    def stop(self):
        if self.released is not None:
            return
        self.released = time.perf_counter()
        self.heard_at_release = self.preview
        self.limit_timer.cancel()
        try:
            self.stream.stop()
        except Exception as error:
            log.warning("microphone stop failed: %s", type(error).__name__)
            self.cleanup_failed = True
        finally:
            try:
                self.stream.close()
            except Exception as error:
                log.warning("microphone close failed: %s", type(error).__name__)
                self.cleanup_failed = True
                self.state.microphone_fault = True
                self.state.notify("마이크를 닫지 못했습니다. Thock을 다시 실행해 주세요.", error=True)
            finally:
                self.loop.call_soon(self.audio.put_nowait, None)
                if self.state.recording is self:
                    self.state.recording = None
                    self.state._sync_sound()

    async def chunks(self):
        while (chunk := await self.audio.get()) is not None:
            remaining = self.max_frames - self.sent_frames
            if remaining <= 0:
                break
            chunk = chunk[:remaining * 2]
            self.sent_frames += len(chunk) // 2
            yield chunk

    async def run(self):
        s, record = self.settings, {"app": self.app}
        outcome, text, stt_ms, total_ms = "failed", "", None, None
        live = field = None
        try:
            grant = await asyncio.to_thread(self.state.account.start_session)
            self.session_id = grant["session_id"]
            self.state.last_session_id = self.session_id
            self.max_frames = int(grant["max_session_seconds"]) * SAMPLE_RATE
            self.limit_timer.cancel()
            remaining = self.max_frames / SAMPLE_RATE - (time.perf_counter() - self.started)
            if self.released is None:
                if remaining <= 0:
                    self.stop()
                else:
                    self.limit_timer = self.loop.call_later(remaining, self.stop)
            if self.previous:
                await asyncio.shield(self.previous)  # earlier dictation finishes writing first
            field = await asyncio.to_thread(InlineField, self.target)
            polish = (lambda words: self.state.polisher.polish(words, self.app)) if s["polish"] else None
            live = self.live = LiveDictation(field.update, polish, self.notes.apply,
                                             lambda message: self.state.notify(message, error=live.blocked),
                                             field.restart)
            live.waiting = field.stopped
            if self.entered:
                live.blocked = True

            def heard(words):
                if self.first_text_at is None and words.strip():
                    self.first_text_at = time.perf_counter()
                if words.strip() != self.preview:
                    self.heard_at = time.perf_counter()
                    self.loop.call_later(TYPING_HOLD, self.state._sync_sound)
                self.preview = words.strip()
                live.update(words)
                self.state._sync_sound()
            raw = await transcribe(self.chunks(), grant["api_key"],
                lambda: self.profile.context(self.app, s["terms"] + self.notes.terms()),
                heard, live.endpoint, finalize_timeout=5)
            self.stop()
            stt_ms = max(0, round((time.perf_counter() - self.released) * 1000))
            if not raw.startswith(self.heard_at_release):  # finalizing dropped or changed words
                record["heard_at_release"] = self.heard_at_release
            text = await live.finish(raw)
            outcome = "empty" if not raw else "delivered"
            if live.error:
                record["polish_error"] = live.error
                outcome = "recovered"  # the raw recognition was kept instead of a correction
            if self.entered:
                record["ended"] = "enter"
            if raw and field.failure:
                # Kept even when Enter ended the dictation: what was sent may have been cut short.
                outcome = "recovered"
                record["input_failure"] = field.failure
                reason = "입력하지 못한 글을 보관했습니다."
                self.state.recover(text or raw, reason)
                self.state.notify(reason + " 막대를 눌러 복사할 수 있습니다.", error=True)
            elif text and s["learn"] and not self.entered:
                self.state.watcher.watch(text)
            total_ms = max(0, round((time.perf_counter() - self.released) * 1000))
        except AccountError as error:
            record["error"] = error.code
            self.state.account_refresh_needed = True
            self.state.notify(str(error), error=True)
        except asyncio.CancelledError:
            outcome = "cancelled"
            if text or self.preview:
                self.state.recover(text or self.preview, "중단된 받아쓰기입니다. 내용을 확인해 주세요.")
            raise
        except Exception as error:
            record["error"] = type(error).__name__
            record["trace"] = [f"{frame.filename.rsplit(chr(92), 1)[-1].rsplit('/', 1)[-1]}:{frame.lineno} {frame.name}"
                               for frame in traceback.extract_tb(error.__traceback__)][-8:]
            log.warning("dictation failed: %s", type(error).__name__)
            if text or self.preview:
                self.state.recover(text or self.preview, "완료하지 못한 받아쓰기입니다. 내용을 확인해 주세요.")
            self.state.notify("받아쓰기를 마치지 못했습니다. 연결을 확인해 주세요.", error=True)
        finally:
            try:
                if live:
                    await live.close()
                self.stop()
                if self.cleanup_failed and outcome == "delivered":
                    outcome = "recovered"
                if self.notices:
                    record["notices"] = list(self.notices)
                if field is not None and field.late_ms is not None:
                    record["late_ms"] = field.late_ms
                record.update(self.timing())
                record.update(recorded_seconds=self.sent_frames / SAMPLE_RATE, outcome=outcome,
                              stt_seconds=stt_ms / 1000 if stt_ms is not None else None,
                              total_seconds=total_ms / 1000 if total_ms is not None else None,
                              time=time.strftime("%Y-%m-%d %H:%M:%S"))
                self.state.report_error(self, record, field)
                if s["learn"] and self.state.settings["learn"] and text:
                    append_history(self.profile.history, {**record, "text": text})
                    self.profile.maybe_rebuild()
                if self.session_id:
                    self.state.queue_report(self.session_id, round(self.sent_frames * 1000 / SAMPLE_RATE),
                                            outcome, input_mode=s["input_mode"], stt_ms=stt_ms, total_ms=total_ms)
            except Exception as error:
                log.warning("dictation cleanup failed: %s", type(error).__name__)
                self.state.notify("글 보관 상태를 확인하지 못했습니다. 설정에서 확인해 주세요.", error=True)
            finally:
                self.finished = True
                self.record = record
                self.state.idle_since = time.perf_counter()
                self.preview = ""
                while not self.audio.empty():
                    self.audio.get_nowait()
                self.state.active.discard(self)
                self.state._sync_sound()
                if not self.done.done():
                    self.done.set_result(None)


class App:
    def __init__(self, settings):
        self.settings = settings
        self.data_root = None
        self.data_lock = threading.RLock()
        self.settings["terms"] = []
        self.notes = TypoNotes(HOME / "signed-out" / "notes.protected")
        self.account = Account()
        self.profile = Profile(HOME / "signed-out" / "profile.protected", HOME / "signed-out" / "history.protected")
        self.polisher = Polisher(settings, self.notes, self.account, self.profile)
        self.profile.complete = self.complete_profile
        self.last_session_id = None
        self.idle_since = time.perf_counter()  # when the last dictation ended, or the app started
        self.recovery = []
        self.reports = {}
        self.notice = ""
        self.notice_until = 0.0
        self.account_refresh_needed = True
        self.watcher = EditWatcher(self.notes)
        self.watcher.enabled = self.settings["learn"]
        self.active = set()
        self.recording = None
        self.pressed_at = 0.0
        self.toggle = False
        self.error_until = 0.0
        self.last = None
        self.levels = deque([0.0] * BARS, maxlen=BARS)  # microphone loudness, newest last
        self.devices_changed = False
        self.microphone_fault = False
        self.open_settings = lambda: None  # set once the settings server exists
        self.open_welcome = lambda: None
        self.open_recovery = lambda: None
        self.sounds = KeyboardSounds()
        self.loop = None

    def account_status(self, force=False):
        status = self.account.status(force=force)
        identity = status.get("account_id")
        if status["state"] == "signed_in" and identity:
            root = HOME / "accounts" / hashlib.sha256(str(identity).encode()).hexdigest()[:24]
            if root != self.data_root and not self.active:
                with self.data_lock:
                    self.data_root = None
                    self.settings["terms"], self.recovery = [], []
                    self.watcher.configure(self.notes, False)
                    try:
                        notes = TypoNotes(root / "notes.protected")
                        profile = Profile(root / "profile.protected", root / "history.protected")
                        terms = read_data(root / "terms.protected", [])
                        recovery = read_data(root / "recovery.protected", [])
                    except (OSError, ValueError):
                        status = {**status, "ready": False,
                                  "error": "이 PC의 계정 자료를 열지 못했습니다. 자료를 보존한 상태로 지원을 요청해 주세요."}
                        self.account.cached = status
                    else:
                        self.notes, self.profile = notes, profile
                        self.profile.complete = self.complete_profile
                        self.profile.can_store = lambda: self.settings["learn"] and self.data_root == root
                        self.settings["terms"], self.recovery = terms, recovery
                        self.polisher = Polisher(self.settings, self.notes, self.account, self.profile)
                        self.watcher.configure(self.notes, self.settings["learn"])
                        self.last_session_id = None
                        self.data_root = root
        elif status["state"] == "signed_out" and not self.active:
            with self.data_lock:
                self.watcher.configure(self.notes, False)
                self.data_root = None
                self.settings["terms"] = []
                self.recovery = []
        return {**status, "personal_key": self.data_root.name if self.data_root else None,
                "welcome_complete": self.settings["welcome_complete"],
                "legacy_available": self.legacy_available()}

    def legacy_available(self):
        return not (HOME / "legacy-account.json").exists() and (
            bool(load_settings().get("terms")) or any((HOME / name).exists()
                for name in ("profile.json", "typo_notes.json", "history.jsonl")))

    def complete_welcome(self, bring_legacy=False):
        status = self.account_status(force=True)
        if not status.get("ready") or not self.data_root:
            raise AccountError("access_unavailable")
        with self.data_lock:
            if not (HOME / "legacy-account.json").exists():
                if bring_legacy and self.legacy_available():
                    import_legacy(HOME, self.data_root)
                    self.data_root = None
                    self.account_status()
                (HOME / "legacy-account.json").write_text(json.dumps({"assigned": True}), encoding="utf-8")
            self.settings["welcome_complete"] = True
            save_settings(self.settings)
        if (self.account.cached.get("error_reports") or {}).get("enabled") is None:
            # The sign-in screen tells the member error reports are sent; settings can turn them off.
            self.account.set_error_reports(True)
            self.account_status(force=True)

    def complete_profile(self, texts):
        if not self.settings["learn"]:
            raise AccountError("learning_paused")
        return self.polisher.complete(PROFILE_PROMPT, json.dumps(texts[:100], ensure_ascii=False), max_tokens=1200,
                                      background=True)

    def report_error(self, session, record, field):
        """Send what failed and where, never what was said, when the member allowed error reports."""
        code = record.get("input_failure") or record.get("error") or record.get("polish_error")
        if not code or (record.get("error") and not record.get("trace")):
            return  # account and service states (time used up, signed out) are not app faults
        if not (self.account.cached.get("error_reports") or {}).get("enabled"):
            return
        stage = ("delivery" if record.get("input_failure") else "polish" if record.get("polish_error")
                 and not record.get("error") else "session")
        details = {"elapsed_ms": round((time.perf_counter() - session.started) * 1000),
                   "overflow_count": session.overflows}
        if session.heard_at is not None:
            details["sound_started_ms"] = round((session.heard_at - session.started) * 1000)
        if field is not None and field.late_ms is not None:
            details["late_ms"] = field.late_ms
        if record.get("trace"):
            details["trace"] = record["trace"]
        self._queue_error(session, stage, code, details)

    def _queue_error(self, session, stage, code, details):
        report = {"stage": stage, "code": code[:48], "app_version": VERSION,
                  "os": f"Windows {platform.version()}"[:48], "target_app": session.app[:64],
                  "details": details, "session_id": session.session_id}
        threading.Thread(target=self._send_error, args=(report,), daemon=True).start()

    def flag_last(self):
        """The member marked the last dictation as wrong: keep its timings to compare with what was said."""
        session = self.last
        if session is None or session.record is None:
            return self.notify("받아쓰기가 끝난 뒤 다시 눌러 주세요.")
        record = session.record
        code = record.get("input_failure") or record.get("error") or record.get("polish_error") or "none"
        details = {**session.timing(), "overflow_count": session.overflows}
        if record.get("late_ms") is not None:
            details["late_ms"] = record["late_ms"]
        log.warning("flagged dictation %s: code=%s %s", record.get("time", "(unfinished)"), code,
                    " ".join(f"{name}={value}" for name, value in details.items()))
        if (self.account.cached.get("error_reports") or {}).get("enabled"):
            self._queue_error(session, "user_flag", code, details)
        self.notify("방금 받아쓰기를 이상함으로 기록했습니다.", seconds=3)  # a receipt, not something to act on

    def _send_error(self, report):
        try:
            self.account.send_error(report)
        except AccountError as error:
            log.info("error report not sent: %s", error.code)

    def notify(self, text, error=False, seconds=12):
        self.notice, self.notice_until = text, time.perf_counter() + seconds
        if error:
            # Every red message the user sees is kept: in the app log and in the dictation it belongs to.
            log.warning("red notice: %s", text)
            session = self.recording or self.last
            if session is not None and session in self.active:
                session.notices.append(text)
            self.flash_error()

    def recover(self, text, reason):
        if not text:
            return
        with self.data_lock:
            self.recovery = (self.recovery + [{"id": uuid.uuid4().hex, "text": text, "reason": reason}])[-5:]
            if self.data_root:
                write_data(self.data_root / "recovery.protected", self.recovery)

    def recovery_action(self, item_id, action):
        with self.data_lock:
            item = next((item for item in self.recovery if item["id"] == item_id), None)
            if not item:
                raise ValueError("missing recovery")
            if action == "copy":
                copy_text(item["text"])
            elif action != "delete":
                raise ValueError("invalid recovery action")
            self.recovery = [row for row in self.recovery if row["id"] != item_id]
            if self.data_root:
                write_data(self.data_root / "recovery.protected", self.recovery)

    def forget_learning(self):
        if not self.data_root or self.active or self.profile.building:
            raise AccountError("session_busy")
        with self.data_lock:
            self.watcher.configure(self.notes, False)
            self.profile.reset()
            self.profile.data = {}
            self.notes.notes = {}
            write_data(self.profile.history, {"total": 0, "rows": []})
            write_data(self.profile.path, {})
            write_data(self.notes.path, {})
            self.watcher.configure(self.notes, self.settings["learn"])

    def queue_report(self, session_id, recorded_ms, outcome, **metrics):
        self.reports[session_id] = (self.account.token, recorded_ms, outcome, metrics)
        asyncio.create_task(self.send_reports())

    async def send_reports(self):
        for session_id, (token, duration, outcome, metrics) in list(self.reports.items()):
            if token != self.account.token:
                self.reports.pop(session_id, None)
                continue
            try:
                await asyncio.to_thread(self.account.report, session_id, duration, outcome, **metrics)
            except AccountError:
                continue
            self.reports.pop(session_id, None)

    async def maintain_account(self):
        last_check = 0
        while True:
            now = time.time()
            retry = self.account.cached.get("state") == "offline"
            if self.account_refresh_needed or now - last_check >= (30 if retry else 900):
                self.account_refresh_needed = False
                try:
                    await asyncio.to_thread(self.account_status, True)
                    if self.data_root:
                        await asyncio.to_thread(history_data, self.profile.history)
                except Exception as error:
                    log.warning("account refresh failed: %s", type(error).__name__)
                last_check = now
            await self.send_reports()
            await asyncio.sleep(15)

    def hotkey_vk(self):
        return HOTKEYS[self.settings["hotkey"]]

    def on_key(self, event):
        """Runs on the asyncio thread; the user chooses hold or toggle. Old settings keep mixed mode."""
        now = time.perf_counter()
        mode = self.recording.settings["input_mode"] if self.recording else self.settings["input_mode"]
        if event == "down":
            if self.recording and self.toggle:
                self._stop()
            elif not self.recording:
                if getattr(self, "microphone_fault", False):
                    self.notify("마이크를 닫지 못했습니다. Thock을 다시 실행해 주세요.", error=True)
                    return
                if not self.settings["welcome_complete"]:
                    self.open_welcome()
                    return
                if not self.account.token or not self.data_root:
                    self.notify("계정을 연결해 주세요. 막대를 누르면 연결 화면이 열립니다.", error=True)
                    return
                self.pressed_at, self.toggle = now, mode == "toggle"
                self.levels.extend([0.0] * BARS)
                self.watcher.flush()  # fixes made to the last paste apply to this dictation
                try:
                    self.recording = self._start_session()
                except Exception:
                    log.warning("microphone unavailable")
                    self.notify("마이크를 확인해 주세요. Windows의 마이크 접근 허용과 입력 장치를 확인하세요.", error=True)
                    return
                self.active.add(self.recording)
                self.last = self.recording
                self._sync_sound()
        elif event == "enter" and self.recording:
            self.recording.enter()
            self._stop()
        elif event == "up" and self.recording and not self.toggle:
            if mode == "auto" and now - self.pressed_at < TAP_SECONDS:
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
        if self.recording:
            self.recording.stop()
        self.recording = None
        self._sync_sound()

    def _sync_sound(self):
        self.sounds.set_mode(selected_mode(self.recording, self.active, self.settings), self.settings["sound_keyboard"])

    def flash_error(self):
        self.error_until = time.perf_counter() + 12

    def status(self):
        """(state, locked) for the overlay; locked means toggle mode is keeping the mic on."""
        if time.perf_counter() < self.error_until:
            return "error", False
        if self.recording:
            return "recording", self.toggle
        if self.active:
            return "processing", False
        if time.perf_counter() < self.notice_until:
            return "notice", False
        return None, False

    def preview(self):
        """The draft lives in the input field; the pill shows notices and, while a dictation waits for a
        text field, the prompt to click one."""
        if time.perf_counter() < self.notice_until:
            return self.notice
        live = getattr(self.last, "live", None)
        return WAITING if live and live.waiting and self.last in self.active else ""

    def public_settings(self):
        s = self.settings
        return {"hotkey": s["hotkey"], "input_mode": s["input_mode"], "input_modes": INPUT_MODES, "polish": s["polish"], "terms": s["terms"], "learn": s["learn"],
                "sound_recording": s["sound_recording"], "sound_processing": s["sound_processing"],
                "sound_keyboard": s["sound_keyboard"], "sound_keyboards": SOUND_KEYBOARDS,
                "preview": s["preview"], "preview_font_ko": s["preview_font_ko"], "preview_font_en": s["preview_font_en"],
                "preview_font_size": s["preview_font_size"], "preview_fonts": PREVIEW_FONTS,
                "preview_font_sizes": list(PREVIEW_FONT_SIZES),
                "notes": self.notes.listing() if self.data_root else [],
                "profile": {**self.profile.data, "building": self.profile.building,
                            "error": self.profile.last_error} if self.data_root else {},
                "personal_ready": self.data_root is not None,
                "personal_key": self.data_root.name if self.data_root else None,
                "recovery": self.recovery,
                "account": self.account.cached, "version": VERSION}

    def update_settings(self, body):
        s = self.settings
        if body.get("input_mode") in INPUT_MODES:
            s["input_mode"] = body["input_mode"]
        if body.get("hotkey") in HOTKEYS:
            s["hotkey"] = body["hotkey"]
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
        if isinstance(body.get("terms"), list) and self.data_root:
            s["terms"] = [t.strip()[:80] for t in body["terms"] if isinstance(t, str) and t.strip()][:500]
            write_data(self.data_root / "terms.protected", s["terms"])
        if self.watcher.enabled != s["learn"]:
            self.watcher.configure(self.notes, s["learn"])
        if "position" in body and body["position"] is None:
            s["position"] = None
        if isinstance(body.get("error_reports"), bool) and self.account.token:
            self.account.set_error_reports(body["error_reports"])
            self.account_status(force=True)
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
    server = SettingsServer(app)
    app.open_settings, app.open_welcome, app.open_recovery = server.open, server.open_welcome, server.open_recovery
    loop = asyncio.new_event_loop()
    app.loop = loop
    threading.Thread(target=loop.run_forever, daemon=True).start()
    threading.Thread(target=run_key_hook, args=(app.hotkey_vk, lambda e: loop.call_soon_threadsafe(app.on_key, e)),
                     daemon=True).start()
    asyncio.run_coroutine_threadsafe(app.maintain_account(), loop)
    log.info("started, hotkey=%s", app.settings["hotkey"])
    if not app.settings["welcome_complete"]:
        app.open_welcome()
    try:
        run_overlay(app)
    finally:
        async def shutdown():
            tasks = [session.task for session in list(app.active)]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout=5)
        except Exception:
            log.warning("shutdown cleanup did not complete within its deadline")
        app.sounds.set_mode(None)
        loop.call_soon_threadsafe(loop.stop)
