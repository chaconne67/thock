import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from thock.config import load_settings, save_settings
from thock.personal import MAGIC, read_data, write_data, import_legacy, history_data, append_history

class SettingsCompatibility(unittest.TestCase):
    def test_existing_mixed_input_terms_and_secrets_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"settings.json").write_text(json.dumps({"hotkey":"scrolllock","terms":["legacy word"],"sound_keyboard":"hhkb"}))
            (root/"secrets.toml").write_text("legacy_data = 'do not touch'")
            before = (root/"secrets.toml").read_bytes()
            with patch("thock.config.HOME",root):
                settings = load_settings()
                self.assertEqual(settings["input_mode"], "auto")
                self.assertEqual(settings["sound_keyboard"], "hhkb")
                settings["terms"] = ["different account"]
                save_settings(settings)
            self.assertEqual((root/"secrets.toml").read_bytes(),before)
            self.assertEqual(json.loads((root/"settings.json").read_text())["terms"],["legacy word"])

    def test_new_install_uses_toggle_and_needs_separate_welcome(self):
        with tempfile.TemporaryDirectory() as temp, patch("thock.config.HOME",Path(temp)):
            settings=load_settings()
            self.assertEqual(settings["input_mode"],"toggle")  # press once to start, again (or Enter) to end
            self.assertFalse(settings["welcome_complete"])

@unittest.skipUnless(sys.platform=="win32","Windows data protection")
class ProtectedStorage(unittest.TestCase):
    def test_roundtrip_has_no_plaintext_and_rejects_damaged_header(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"notes.protected"
            value={"private":"테스트 문장"}
            write_data(path,value)
            self.assertTrue(path.read_bytes().startswith(MAGIC))
            self.assertNotIn("테스트 문장".encode(),path.read_bytes())
            self.assertEqual(read_data(path),value)
            path.write_text('{"private":"plaintext"}')
            with self.assertRaises(ValueError):read_data(path)

    def test_explicit_import_verifies_copy_and_preserves_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            original=b'{"old":{"to":"new","count":3}}'
            (root/"typo_notes.json").write_bytes(original)
            target=root/"accounts"/"test"
            import_legacy(root,target)
            self.assertEqual((root/"typo_notes.json").read_bytes(),original)
            self.assertEqual(read_data(target/"notes.protected"),json.loads(original))

    def test_history_retention_keeps_total_count_for_profile_schedule(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"history.protected"
            write_data(path,{"total":1000,"rows":[{"text":"old","saved_at":0}]})
            append_history(path,{"text":"current"})
            data=history_data(path)
            self.assertEqual(data["total"],1001)
            self.assertEqual([r["text"] for r in data["rows"]],["current"])

    def test_expired_history_is_removed_on_read_without_a_new_dictation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"history.protected"
            write_data(path, {"total": 100, "rows": [{"text": "expired", "saved_at": 1}]})
            with patch("thock.personal.time.time", return_value=31*86400):
                self.assertEqual(history_data(path), {"total": 100, "rows": []})
            self.assertEqual(read_data(path), {"total": 100, "rows": []})
