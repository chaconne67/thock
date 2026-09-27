"""Thock Voice Typing: hold CapsLock, speak, release -> corrected text is pasted at the cursor.

Path: key hook -> microphone -> Soniox real-time STT -> LLM correction (OpenRouter) -> clipboard paste.
A small pill above the taskbar shows the state; hover it for settings, drag it to move it.
Settings and keys live in ~/.voicetype (never in Git). The code is in the thock package.
"""

from thock.app import main

if __name__ == "__main__":
    main()
