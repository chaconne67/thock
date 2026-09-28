"""Loop one keyboard recording for the active dictation phase on Windows."""

from pathlib import Path

from .config import log


SOUNDS = {"recording": "recording.wav", "processing": "processing.wav"}


def selected_mode(recording, active, settings):
    """Recording takes precedence if an older session is still processing."""
    if recording is not None and recording in active:
        return "recording" if settings["sound_recording"] else None
    if active:
        return "processing" if settings["sound_processing"] else None
    return None


class KeyboardSounds:
    def __init__(self):
        self.mode = None

    def set_mode(self, mode):
        if mode == self.mode:
            return
        import winsound

        try:
            winsound.PlaySound(None, 0)
            self.mode = None
            if mode is not None:
                path = Path(__file__).resolve().parent / "sounds" / SOUNDS[mode]
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC
                                   | winsound.SND_LOOP | winsound.SND_NODEFAULT)
                self.mode = mode
        except RuntimeError as exc:
            log.warning("keyboard sound unavailable: %s", exc)
