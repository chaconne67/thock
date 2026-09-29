"""Profile terms must reflect repeated use across separate dictations."""

import json
import tempfile
import unittest
from pathlib import Path

from thock.learning import Profile


class ProfileTerms(unittest.TestCase):
    def build(self, texts, candidates):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.jsonl"
            history.write_text(
                "".join(json.dumps({"text": text}, ensure_ascii=False) + "\n" for text in texts),
                encoding="utf-8",
            )
            profile = Profile(root / "profile.json", history)
            request = {}

            def complete(texts):
                request["texts"] = texts
                return json.dumps(
                    {"domain": "소프트웨어 개발", "topics": ["음성 입력"], "terms": candidates},
                    ensure_ascii=False,
                )

            profile.complete = complete
            profile._build()
            return profile.data, request, profile.context("editor", ["직접 등록"])

    def test_only_repeated_terms_are_saved_and_used(self):
        first = "Thock으로 GBrain을 정리해 줘."
        texts = [
            first,
            "Thock 설정을 열어 줘.",
            "Thock에서 GBrain 기록을 찾아 줘.",
            "HTML 이야기를 한다. HTML은 한 번의 받아쓰기에서만 말했다.",
            first,
        ]
        data, request, context = self.build(
            texts, ["GBrain", "HTML", "TM", "Thock", "처음 보는 말"],
        )
        self.assertEqual(request["texts"], list(reversed(texts)))
        self.assertEqual(data["terms"], ["Thock", "GBrain"])
        self.assertEqual(data["built_at"], len(texts))
        self.assertEqual(context["terms"], ["직접 등록", "Thock", "GBrain"])

    def test_no_repeated_term_does_not_turn_one_offs_into_frequent_terms(self):
        data, _, context = self.build(
            ["Leopold 키보드를 시험했다.", "HHKB 키보드를 시험했다."],
            ["Leopold", "HHKB"],
        )
        self.assertEqual(data["domain"], "소프트웨어 개발")
        self.assertEqual(data["terms"], [])
        self.assertEqual(context["terms"], ["직접 등록"])


if __name__ == "__main__":
    unittest.main()
