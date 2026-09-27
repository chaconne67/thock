"""Learning: the word fixes the user makes after a paste, and a profile of their field and vocabulary."""

import difflib
import json
import os
import threading
import time
import unicodedata

from .config import log
from .correction import sentence_levels


PROFILE_PROMPT = """You keep a short profile that helps a dictation app spell this user's words correctly.
The user message holds texts this user dictated recently, one per line. From those texts only:
- "domain": the user's field or work, in a few words
- "topics": up to 8 recurring subjects
- "terms": up to 150 names, product names, jargon and code words the user actually used, spelled exactly as in the texts
Do not guess beyond the texts. Write domain and topics in the language the user mostly writes in.
Reply with JSON only: {"domain": "...", "topics": ["..."], "terms": ["..."]}"""


def _norm(s):
    """Letters and digits only, lower-case: spacing, punctuation and case edits are not word fixes."""
    return "".join(c for c in s.lower() if not c.isspace() and not unicodedata.category(c).startswith("P"))


def word_fixes(old, new):
    """Word replacements between what we pasted (old) and what the user left (new).
    Appended text, pure deletions and spacing/punctuation edits are ignored; a rewrite yields nothing."""
    zones = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        # changes separated only by spaces belong to one phrase ("클로드 코드" -> "Claude Code")
        if zones and not old[zones[-1][1]:i1].strip() and not new[zones[-1][3]:j1].strip():
            zones[-1][1], zones[-1][3] = i2, j2
        else:
            zones.append([i1, i2, j1, j2])
    if sum(i2 - i1 for i1, i2, _, _ in zones) > len(old) / 2:
        return []
    fixes = []
    for i1, i2, j1, j2 in zones:
        if i1 == i2 or j1 == j2:
            continue  # typing more or deleting is not a fix
        while i1 > 0 and not old[i1 - 1].isspace():  # widen to whole words
            i1 -= 1
        while i2 < len(old) and not old[i2].isspace():
            i2 += 1
        while j1 > 0 and not new[j1 - 1].isspace():
            j1 -= 1
        while j2 < len(new) and not new[j2].isspace():
            j2 += 1
        o, n = old[i1:i2], new[j1:j2]
        k = len(os.path.commonprefix([o[::-1], n[::-1]]))
        if 0 < k <= 2 and len(o) > k and len(n) > k:  # drop a shared particle or period: 펀드키퍼에 -> 펀드키퍼
            o, n = o[:-k], n[:-k]
        if _norm(o) != _norm(n) and 0 < len(o) <= 40 and 0 < len(n) <= 40:
            fixes.append((o, n))
    return fixes if len(fixes) <= 3 else []


def fixes_in_field(pasted, before, after):
    """Fixes inside our pasted text, given the field right after the paste and now.
    None means the field moved on (sent, cleared or edited elsewhere) and watching should stop."""
    i = before.rfind(pasted)
    if i < 0 or not pasted.strip():
        return None
    head, tail = before[:i], before[i + len(pasted):]
    if len(after) < len(head) + len(tail) or not (after.startswith(head) and after.endswith(tail)):
        return None
    ours = after[len(head):len(after) - len(tail)]
    return word_fixes(pasted, ours) if ours.strip() else None  # our text is gone: sent or cleared


class TypoNotes:
    """old -> {"to": new, "count": n}. Seen twice: used for recognition and correction.
    Seen three times (or added by hand): replaced before pasting."""
    CONFIRM, AUTO = 2, 3

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        self.notes = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _save(self):
        self.path.write_text(json.dumps(self.notes, ensure_ascii=False, indent=2), encoding="utf-8")

    def record(self, old, new):
        with self.lock:
            note = self.notes.get(old)
            if note and note["to"] == new:
                note["count"] += 1
            else:
                self.notes[old] = {"to": new, "count": 1}
            self._save()
        log.info("typo note: %s -> %s (%d)", old, new, self.notes[old]["count"])

    def add(self, old, new):
        with self.lock:
            self.notes[old] = {"to": new, "count": self.AUTO}
            self._save()

    def delete(self, old):
        with self.lock:
            self.notes.pop(old, None)
            self._save()

    def _confirmed(self):
        return [(o, n["to"], n["count"]) for o, n in list(self.notes.items()) if n["count"] >= self.CONFIRM]

    def terms(self):
        return [to for _, to, _ in self._confirmed()]

    def hint(self):
        return ", ".join(f"{o} → {to}" for o, to, _ in self._confirmed())

    def _auto(self, old, count):
        return count >= self.AUTO and len(old) >= 2  # one-letter words appear inside too many others

    def apply(self, text):
        for o, to, count in self._confirmed():
            if self._auto(o, count):
                text = text.replace(o, to)
        return text

    def listing(self):
        return [{"old": o, "new": n["to"], "count": n["count"],
                 "state": "auto" if self._auto(o, n["count"]) else "on" if n["count"] >= self.CONFIRM else "seen"}
                for o, n in sorted(self.notes.items(), key=lambda kv: -kv[1]["count"])]


class Profile:
    """What Thock has learned about the user's field and vocabulary from their own dictations.
    Rebuilt in the background every EVERY dictations by one LLM call; feeds recognition and correction."""
    EVERY, FIRST, SAMPLE_CHARS = 50, 20, 6000

    def __init__(self, path, history):
        self.path, self.history, self.lock, self.building = path, history, threading.Lock(), False
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.complete = None  # set to Polisher.complete once it exists

    def terms(self):
        return self.data.get("terms", [])

    def summary(self):
        topics = ", ".join(self.data.get("topics", []))
        return f"{self.data['domain']} ({topics})" if self.data.get("domain") else ""

    def usual_level(self):
        """The speech level the user normally dictates in ('casual'/'polite'), or None if not clear.
        Recomputed only when the history file has grown."""
        size = self.history.stat().st_size if self.history.exists() else 0
        if getattr(self, "_level_at", None) != size:
            levels = [lv for text in self._texts()[-200:] for lv in sentence_levels(text)]
            share = levels.count("casual") / len(levels) if len(levels) >= 10 else 0.5
            self._level, self._level_at = ("casual" if share >= 0.7 else "polite" if share <= 0.3 else None), size
        return self._level

    def context(self, app, terms):
        """Soniox context: short key-value facts plus the vocabulary to spell right."""
        general = [{"key": "application", "value": app}]
        if self.data.get("domain"):
            general.append({"key": "domain", "value": self.data["domain"]})
        if self.data.get("topics"):
            general.append({"key": "topics", "value": ", ".join(self.data["topics"])})
        return {"general": general, "terms": list(dict.fromkeys(terms + self.terms()))}

    def _texts(self):
        if not self.history.exists():
            return []
        rows = [json.loads(line) for line in self.history.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r["text"] for r in rows if r.get("text")]

    def maybe_rebuild(self):
        """Called after each dictation: rebuild once enough new text has piled up."""
        count = len(self._texts())
        due = count - self.data.get("built_at", 0) >= self.EVERY if self.data else count >= self.FIRST
        if due and not self.building:
            self.rebuild()

    def rebuild(self):
        self.building = True
        threading.Thread(target=self._build, daemon=True).start()

    def _build(self):
        try:
            texts, sample = self._texts(), []
            for text in reversed(texts):  # newest first, up to the size budget
                if sum(map(len, sample)) + len(text) > self.SAMPLE_CHARS:
                    break
                sample.append(text)
            if not sample:
                return
            answer = self.complete(PROFILE_PROMPT, "\n".join(dict.fromkeys(sample)))
            found = json.loads(answer[answer.find("{"):answer.rfind("}") + 1])
            data = {"domain": str(found.get("domain", ""))[:80],
                    "topics": [str(t)[:40] for t in found.get("topics", [])][:8],
                    "terms": [str(t)[:40] for t in found.get("terms", []) if str(t).strip()][:150],
                    "built_at": len(texts), "updated": time.strftime("%Y-%m-%d %H:%M")}
            with self.lock:
                self.data = data
                self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("profile rebuilt: %s, %d terms", data["domain"], len(data["terms"]))
        except Exception:
            log.exception("profile rebuild failed")
        finally:
            self.building = False

    def reset(self):
        """Forget the profile; learning starts again after EVERY new dictations."""
        with self.lock:
            self.data = {"built_at": len(self._texts())}
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
