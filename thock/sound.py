"""Play the phase-specific keyboard recording with a short fade on stop."""

import array
import math
import threading
import wave
from pathlib import Path

from .config import log


SOUNDS = {
    "rainy75": {"recording": "recording.wav", "processing": "processing.wav"},
    "ikki68": {"recording": "ikki68-recording.wav", "processing": "ikki68-processing.wav"},
    "hhkb": {"recording": "hhkb-recording.wav", "processing": "hhkb-processing.wav"},
    "leopold": {"recording": "leopold-recording.wav", "processing": "leopold-processing.wav"},
    "technics": {"recording": "technics-recording.wav", "processing": "technics-processing.wav"},
    "keychron": {"recording": "keychron-recording.wav", "processing": "keychron-processing.wav"},
}
RATE, BLOCK_FRAMES, FADE_FRAMES = 48000, 480, 10560  # 10 ms blocks; 220 ms stop fade


def selected_mode(recording, active, settings):
    """The current session owns audio; recognized live text is already processing."""
    if recording is not None and recording in active:
        if settings["sound_recording"]:
            return "recording"
        if getattr(recording, "preview", "") and settings["sound_processing"]:
            return "processing"
        return None
    if active:
        return "processing" if settings["sound_processing"] else None
    return None


class KeyboardSounds:
    def __init__(self):
        self.mode = None
        self._lock = threading.Lock()
        self._worker = None
        self._audio = {}

    def set_mode(self, phase, keyboard="rainy75"):
        if phase is not None and (keyboard not in SOUNDS or phase not in SOUNDS[keyboard]):
            raise ValueError(f"unknown keyboard sound: {keyboard}/{phase}")
        mode = (keyboard, phase) if phase is not None else None
        with self._lock:
            if mode == self.mode:
                return
            self.mode = mode
            if mode is not None and self._worker is None:
                self._start_locked()

    def _start_locked(self):
        self._worker = threading.Thread(target=self._play, name="thock-keyboard-sound", daemon=True)
        self._worker.start()

    def _read_audio(self, mode):
        if mode not in self._audio:
            path = Path(__file__).resolve().parent / "sounds" / SOUNDS[mode[0]][mode[1]]
            with wave.open(str(path), "rb") as source:
                if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, RATE):
                    raise ValueError(f"unsupported keyboard sound format: {path}")
                self._audio[mode] = source.readframes(source.getnframes())
        return self._audio[mode]

    def _play(self):
        current = requested = None
        try:
            with self._lock:
                requested = self.mode
            if requested is None:
                return
            import sounddevice as sd

            with sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK_FRAMES) as output:
                audio, position, fade = b"", 0, None
                while True:
                    with self._lock:
                        requested = self.mode
                    if current is None:
                        if requested is None:
                            break
                        current, audio, position, fade = requested, self._read_audio(requested), 0, None
                    elif requested != current and fade is None:
                        fade = 0

                    size = BLOCK_FRAMES * 2
                    block = audio[position:position + size]
                    if len(block) < size:
                        block += audio[:size - len(block)]
                    position = (position + size) % len(audio)
                    if fade is not None:
                        samples = array.array("h", block)
                        for i, sample in enumerate(samples):
                            samples[i] = round(sample * math.cos(math.pi * min(fade + i + 1, FADE_FRAMES)
                                                                 / (2 * FADE_FRAMES)))
                        block = samples.tobytes()
                        fade += BLOCK_FRAMES
                    output.write(block)
                    if fade is not None and fade >= FADE_FRAMES:
                        current = None
        except Exception as exc:
            log.warning("keyboard sound unavailable: %s", exc)
            with self._lock:
                if self.mode == requested:
                    self.mode = None
        finally:
            with self._lock:
                self._worker = None
                if self.mode is not None:
                    self._start_locked()
