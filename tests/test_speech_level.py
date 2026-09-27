"""How a dictation's speech level (반말/존댓말) is read. Run: uv run python -m unittest tests.test_speech_level"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from thock.correction import sentence_levels, speech_level_rule  # noqa: E402

KEEP = "문장 끝 말투(반말·존댓말)는 말한 그대로 둔다."


class SentenceLevels(unittest.TestCase):
    def test_endings(self):
        self.assertEqual(sentence_levels("맞을 것 같아요. 좋을 것 같다, 확인을 해봐라."), ["polite", "casual"])
        self.assertEqual(sentence_levels("보내 드렸습니다. 확인 부탁드립니다."), ["polite", "polite"])

    def test_connective_endings_are_casual(self):
        self.assertEqual(sentence_levels("그걸로 만드는 거니까. 봐야지."), ["casual", "casual"])

    def test_quoted_examples_do_not_count(self):
        self.assertEqual(sentence_levels('예를 들면 "값을 입력하는 겁니다." 이렇게 안내하면 돼.'), ["casual"])


class SpeechLevelRule(unittest.TestCase):
    def test_stray_polite_sentence_follows_the_majority(self):
        rule = speech_level_rule("맞을 것 같아요. 진행하면 좋을 것 같다. 확인해 봐라.", usual=None)
        self.assertIn("반말로 말한", rule)

    def test_stray_casual_sentence_follows_the_majority(self):
        rule = speech_level_rule("자료 보내 드렸습니다. 확인 부탁드립니다. 내일 봐.", usual="casual")
        self.assertIn("존댓말로 말한", rule)

    def test_tie_goes_to_the_usual_level(self):
        self.assertIn("반말로 말한", speech_level_rule("커밋해 줘. 테스트도 돌려 주세요.", usual="casual"))
        self.assertEqual(speech_level_rule("커밋해 줘. 테스트도 돌려 주세요.", usual=None), KEEP)

    def test_one_level_throughout_is_kept(self):
        self.assertEqual(speech_level_rule("안녕하세요. 자료 보내 드렸습니다.", usual="casual"), KEEP)
        self.assertEqual(speech_level_rule("확인해 주세요.", usual="casual"), KEEP)
        self.assertEqual(speech_level_rule("커밋하고 푸시해 줘.", usual="polite"), KEEP)


if __name__ == "__main__":
    unittest.main()
