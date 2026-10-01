"""Play the selected keyboard's typing loop with a short fade on stop."""

import array
import math
import threading
import wave
from pathlib import Path

from .config import log


SOUNDS = {
    "rainy75": "processing.wav",
    "ikki68": "ikki68-processing.wav",
    "hhkb": "hhkb-processing.wav",
    "leopold": "leopold-processing.wav",
    "technics": "technics-processing.wav",
    "keychron": "keychron-processing.wav",
}
RATE, BLOCK_FRAMES, FADE_FRAMES = 48000, 480, 10560  # 10 ms blocks; 220 ms stop fade


def selected_mode(recording, active, settings):
    """Whether typing plays: while recording only as speech turns into text, then until the text is written."""
    if not settings["sound_processing"]:
        return False
    if recording is not None and recording in active:
        return getattr(recording, "typing", False)
    return bool(active)


class KeyboardSounds:
    def __init__(self):
        self.mode = None
        self._lock = threading.Lock()
        self._worker = None
        self._audio = {}

    def set_mode(self, playing, keyboard="rainy75"):
        if playing and keyboard not in SOUNDS:
            raise ValueError(f"unknown keyboard sound: {keyboard}")
        mode = keyboard if playing else None
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
            path = Path(__file__).resolve().parent / "sounds" / SOUNDS[mode]
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
