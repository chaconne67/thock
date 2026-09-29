"""Client-side speech-level detection and the Thock correction request."""

import re

POLITE_ENDING = re.compile(r"(요|니다|습니까|십시오|세요|죠)$")
QUOTED = re.compile(r'["“][^"”]*["”]|[\'‘][^\'’]*[\'’]')
SPEECH_LEVEL = {"casual": "반말", "polite": "존댓말"}


def sentence_levels(text):
    """'casual' or 'polite' for each sentence ending in Hangul; quoted examples do not count."""
    levels = []
    for sentence in re.split(r"(?<=[.?!])\s+|\n+", QUOTED.sub("", text)):
        sentence = sentence.strip().rstrip(".?!,~ ")
        if sentence and "가" <= sentence[-1] <= "힣":
            levels.append("polite" if POLITE_ENDING.search(sentence) else "casual")
    return levels


def speech_level_rule(text, usual=None):
    """The correction rule for sentence endings in this dictation."""
    levels = sentence_levels(text)
    casual, polite = levels.count("casual"), levels.count("polite")
    if casual and polite:
        spoken = "casual" if casual > polite else "polite" if polite > casual else usual
        if spoken:
            stray = "polite" if spoken == "casual" else "casual"
            return (f"이번 글은 {SPEECH_LEVEL[spoken]}로 말한 것이다. 음성 인식이 끝말을 잘못 들어 섞였으니 "
                    f"{SPEECH_LEVEL[stray]}로 끝난 문장만 끝말을 {SPEECH_LEVEL[spoken]}로 고친다.")
    return "문장 끝 말투(반말·존댓말)는 말한 그대로 둔다."


class Polisher:
    def __init__(self, settings, notes, account, profile):
        self.settings, self.notes, self.account, self.profile = settings, notes, account, profile

    def polish(self, text, app):
        terms = [term for term in dict.fromkeys(self.settings["terms"] + self.profile.terms()[:60])
                 if isinstance(term, str) and len(term) <= 80][:60]
        fixes = self.notes.hint()
        if len(fixes) > 1000:
            fixes = fixes[:1000].rsplit(", ", 1)[0]
        return self.account.complete("polish", {
            "text": text,
            "terms": terms,
            "fixes": fixes or "없음",
            "profile": self.profile.summary() or "아직 모름",
            "app": app,
            "register": speech_level_rule(text, self.profile.usual_level()),
        })
