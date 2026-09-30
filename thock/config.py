"""Paths, fixed values and the user's settings (~/.voicetype, never in Git)."""

import json
import logging
import tomllib
from pathlib import Path


APP_NAME, VERSION = "Thock", "0.1.3"
HOME = Path.home() / ".voicetype"
SAMPLE_RATE = 16000
SONIOX_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
SONIOX_MODEL = "stt-rt-v5"
POLISH_MODEL = "openai/gpt-6-luna"  # via OpenRouter; chosen by a 16+8 sentence comparison (docs)
TAP_SECONDS = 0.35  # shorter press = toggle mode, longer press = push-to-talk
HOTKEYS = {"capslock": 0x14, "scrolllock": 0x91}
SOUND_KEYBOARDS = {"rainy75": "Rainy75", "ikki68": "Ikki68 Aurora · WS Brown",
                   "hhkb": "HHKB Professional Hybrid", "leopold": "Leopold FC660M",
                   "technics": "Technics · Gateron Yellow", "keychron": "Keychron K10 · Linear"}
DEFAULTS = {"hotkey": "capslock", "polish": True, "polish_provider": "chatgpt",  # settings.json
            "terms": [], "position": None, "learn": True,
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
    keys_path, settings_path = HOME / "secrets.toml", HOME / "settings.json"
    keys = tomllib.loads(keys_path.read_text(encoding="utf-8")) if keys_path.exists() else {}
    stored = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    settings = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS},
                "soniox_api_key": keys.get("soniox_api_key", ""), "openrouter_api_key": keys.get("openrouter_api_key", "")}
    if not isinstance(settings["sound_keyboard"], str) or settings["sound_keyboard"] not in SOUND_KEYBOARDS:
        settings["sound_keyboard"] = DEFAULTS["sound_keyboard"]
    for lang, fonts in PREVIEW_FONTS.items():
        if not isinstance(settings[f"preview_font_{lang}"], str) or settings[f"preview_font_{lang}"] not in fonts:
            settings[f"preview_font_{lang}"] = DEFAULTS[f"preview_font_{lang}"]
    return settings


def save_settings(s):
    HOME.mkdir(exist_ok=True)
    (HOME / "settings.json").write_text(json.dumps({k: s[k] for k in DEFAULTS}, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    # json.dumps of a string is a valid TOML basic string
    (HOME / "secrets.toml").write_text(f"soniox_api_key = {json.dumps(s['soniox_api_key'])}\n"
                                       f"openrouter_api_key = {json.dumps(s['openrouter_api_key'])}\n", encoding="utf-8")
