"""Paths, fixed values and the user's settings (~/.voicetype, never in Git)."""

import json
import logging
from pathlib import Path


APP_NAME, VERSION = "Thock", "0.3.0"
HOME = Path.home() / ".voicetype"
SAMPLE_RATE = 16000
SONIOX_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
SONIOX_MODEL = "stt-rt-v5"
INPUT_MODES = {"hold": "누르는 동안", "toggle": "한 번씩 눌러 시작·종료", "auto": "기존 방식 (누르기 + 짧게 두 번)"}
TAP_SECONDS = 0.35  # shorter press = toggle mode, longer press = push-to-talk
HOTKEYS = {"capslock": 0x14, "scrolllock": 0x91}
SOUND_KEYBOARDS = {"rainy75": "Rainy75", "ikki68": "Ikki68 Aurora · WS Brown",
                   "hhkb": "HHKB Professional Hybrid", "leopold": "Leopold FC660M",
                   "technics": "Technics · Gateron Yellow", "keychron": "Keychron K10 · Linear"}
DEFAULTS = {"hotkey": "capslock", "polish": True, "polish_provider": "chatgpt",  # keep old setting without using it
            "terms": [], "position": None, "learn": True, "input_mode": "hold", "welcome_complete": False,
            "sound_recording": False, "sound_processing": True, "sound_keyboard": "rainy75",
            "preview": True, "preview_font_ko": "Noto Sans KR", "preview_font_en": "Inter", "preview_font_size": 14}
PREVIEW_FONT_SIZES = range(11, 21)  # pixels at 100% display scaling
# Bundled in thock/fonts (family name: label). Korean fonts also carry Latin letters;
# the English font is used only when the text has no Hangul.
PREVIEW_FONTS = {"ko": {"Noto Sans KR": "본고딕", "Pretendard": "프리텐다드", "NanumGothic": "나눔고딕",
                        "NanumBarunGothic": "나눔바른고딕"},
                 "en": {"Inter": "Inter", "Roboto": "Roboto", "Source Sans 3": "Source Sans 3"}}


log = logging.getLogger("voicetype")


def load_settings():
    settings_path = HOME / "settings.json"
    stored = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    settings = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    # An existing installation retains its short-tap/hold behavior until the user chooses.
    if stored and "input_mode" not in stored:
        settings["input_mode"] = "auto"
    if settings["input_mode"] not in INPUT_MODES:
        settings["input_mode"] = DEFAULTS["input_mode"]
    if not isinstance(settings["sound_keyboard"], str) or settings["sound_keyboard"] not in SOUND_KEYBOARDS:
        settings["sound_keyboard"] = DEFAULTS["sound_keyboard"]
    for lang, fonts in PREVIEW_FONTS.items():
        if not isinstance(settings[f"preview_font_{lang}"], str) or settings[f"preview_font_{lang}"] not in fonts:
            settings[f"preview_font_{lang}"] = DEFAULTS[f"preview_font_{lang}"]
    return settings


def save_settings(s):
    HOME.mkdir(exist_ok=True)
    previous = json.loads((HOME / "settings.json").read_text(encoding="utf-8")) if (HOME / "settings.json").exists() else {}
    values = {k: s[k] for k in DEFAULTS if k != "terms"}
    if "terms" in previous:
        values["terms"] = previous["terms"]
    (HOME / "settings.json").write_text(json.dumps(values, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
