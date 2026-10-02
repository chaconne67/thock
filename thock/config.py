"""Paths, fixed values and the user's settings (~/.voicetype, never in Git)."""

import json
import logging
import os
from pathlib import Path


APP_NAME, VERSION = "Thock", "0.5.1.dev9"
# Inside Crema (Crema's 9,900원 plan): Crema starts Thock with THOCK_EMBEDDED=1, its data folder (THOCK_HOME),
# its server and sign-in (account.py) and its own path (THOCK_HOST_EXE). Unset, Thock is the Thock app.
EMBEDDED = os.environ.get("THOCK_EMBEDDED") == "1"
HOME = Path(os.environ["THOCK_HOME"]) if os.environ.get("THOCK_HOME") else Path.home() / ".voicetype"
SAMPLE_RATE = 16000
SONIOX_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
SONIOX_MODEL = "stt-rt-v5"
INPUT_MODES = {"hold": "누르고 있는 동안 녹음", "toggle": "눌러서 녹음 켜고 끄기"}
# How far the editor goes (주인님 결정 2026-10-02); its instructions per level are in correction.py.
POLISH_LEVELS = {"verbatim": "말한 그대로", "clean": "군더더기만 빼기", "smooth": "읽기 좋게 다듬기"}
# Styles the whole dictation can be rewritten in once, after the key is released (주인님 결정 2026-10-02);
# what the writer is told for each is in correction.STYLES. "custom" is the user's own line (style_custom).
STYLES = {"none": "바꾸지 않음", "bullets": "개조식", "email": "이메일", "written": "문어체", "polite": "존댓말로",
          "casual": "반말로", "friendly": "부드러운 대화체", "mz": "MZ 말투", "seoul90": "90년대 서울말",
          "gyeongsang": "경상도 말씨", "jeolla": "전라도 말씨", "chungcheong": "충청도 말씨", "jeju": "제주 말씨",
          "custom": "직접 적기"}
STYLE_CUSTOM_MAX = 200
HOTKEYS = {"capslock": 0x14, "scrolllock": 0x91}
SOUND_KEYBOARDS = {"rainy75": "Rainy75", "ikki68": "Ikki68 Aurora · WS Brown",
                   "hhkb": "HHKB Professional Hybrid", "leopold": "Leopold FC660M",
                   "technics": "Technics · Gateron Yellow", "keychron": "Keychron K10 · Linear"}
DEFAULTS = {"hotkey": "capslock", "polish": True, "polish_level": "clean", "style": "none", "style_custom": "",
            "polish_provider": "chatgpt",  # keep old setting without using it
            "terms": [], "position": None, "learn": True, "input_mode": "toggle", "welcome_complete": False,
            "keep_audio": False, "sound_processing": True, "sound_keyboard": "rainy75",
            "microphone": None,  # a microphone's name; None is Windows' default microphone
            "preview": True, "preview_font_ko": "Noto Sans KR", "preview_font_en": "Inter", "preview_font_size": 14}
PREVIEW_FONT_SIZES = range(11, 21)  # pixels at 100% display scaling
# Bundled in thock/fonts (family name: label). Korean fonts also carry Latin letters;
# the English font is used only when the text has no Hangul.
PREVIEW_FONTS = {"ko": {"Noto Sans KR": "본고딕", "Pretendard": "프리텐다드", "NanumGothic": "나눔고딕",
                        "NanumBarunGothic": "나눔바른고딕"},
                 "en": {"Inter": "Inter", "Roboto": "Roboto", "Source Sans 3": "Source Sans 3"}}


log = logging.getLogger("voicetype")
# One JSON line per dictation and per refused key press: what happened and when, as numbers and codes.
# Never the dictated text, the keys typed or window titles. Written to trace.log, rotated by size.
trace = logging.getLogger("voicetype.trace")


def load_settings():
    settings_path = HOME / "settings.json"
    stored = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    settings = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    if stored.get("sound_recording") is True:  # its switch merged into the one typing-sound switch (0.4.6)
        settings["sound_processing"] = True
    if settings["input_mode"] not in INPUT_MODES:
        settings["input_mode"] = DEFAULTS["input_mode"]
    if not isinstance(settings["polish_level"], str) or settings["polish_level"] not in POLISH_LEVELS:
        settings["polish_level"] = DEFAULTS["polish_level"]
    if not isinstance(settings["style_custom"], str):
        settings["style_custom"] = ""
    if (not isinstance(settings["style"], str) or settings["style"] not in STYLES
            or (settings["style"] == "custom" and not settings["style_custom"].strip())):
        settings["style"] = DEFAULTS["style"]
    if not isinstance(settings["sound_keyboard"], str) or settings["sound_keyboard"] not in SOUND_KEYBOARDS:
        settings["sound_keyboard"] = DEFAULTS["sound_keyboard"]
    if not isinstance(settings["microphone"], str):
        settings["microphone"] = None
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
