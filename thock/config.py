"""Paths, fixed values and the user's settings (~/.voicetype, never in Git)."""

import json
import logging
import tomllib
from pathlib import Path


APP_NAME, VERSION = "Thock", "0.1"
HOME = Path.home() / ".voicetype"
SAMPLE_RATE = 16000
SONIOX_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
SONIOX_MODEL = "stt-rt-v5"
POLISH_MODEL = "openai/gpt-6-luna"  # via OpenRouter; chosen by a 16+8 sentence comparison (docs)
TAP_SECONDS = 0.35  # shorter press = toggle mode, longer press = push-to-talk
HOTKEYS = {"capslock": 0x14, "scrolllock": 0x91}
DEFAULTS = {"hotkey": "capslock", "polish": True, "polish_provider": "chatgpt",  # settings.json
            "terms": [], "position": None, "learn": True}


log = logging.getLogger("voicetype")


def load_settings():
    keys_path, settings_path = HOME / "secrets.toml", HOME / "settings.json"
    keys = tomllib.loads(keys_path.read_text(encoding="utf-8")) if keys_path.exists() else {}
    stored = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS},
            "soniox_api_key": keys.get("soniox_api_key", ""), "openrouter_api_key": keys.get("openrouter_api_key", "")}


def save_settings(s):
    HOME.mkdir(exist_ok=True)
    (HOME / "settings.json").write_text(json.dumps({k: s[k] for k in DEFAULTS}, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    # json.dumps of a string is a valid TOML basic string
    (HOME / "secrets.toml").write_text(f"soniox_api_key = {json.dumps(s['soniox_api_key'])}\n"
                                       f"openrouter_api_key = {json.dumps(s['openrouter_api_key'])}\n", encoding="utf-8")
