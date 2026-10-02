"""Updating itself: only forward, only a download matching its SHA-256. No network is used."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class Update(unittest.TestCase):
    def test_only_a_newer_version_is_taken(self):
        from thock.update import newer
        self.assertTrue(newer("0.5.2", "0.5.1"))
        self.assertTrue(newer("0.5.2", "0.5.2.dev3"))  # the release after its development builds
        self.assertTrue(newer("0.5.2.dev2", "0.5.2.dev1"))
        self.assertFalse(newer("0.5.1", "0.5.2.dev1"))  # a development build is not taken back
        self.assertFalse(newer("0.5.1", "0.5.1"))
        self.assertFalse(newer("unknown", "0.5.1"))

    def test_the_installer_is_kept_only_when_it_matches_the_servers_sha256(self):
        import thock.update as update
        body = b"installer"
        with tempfile.TemporaryDirectory() as temp, patch.object(update, "FOLDER", Path(temp)), \
                patch("thock.update.VERSION", "0.5.1"), \
                patch("thock.update.urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(body)):
            offer = {"version": "0.5.2", "url": "/downloads/Thock-setup-0.5.2.exe",
                     "sha256": hashlib.sha256(body).hexdigest()}
            with patch("thock.update.Account._request", return_value=offer):
                version, path = update.fetch()
            self.assertEqual((version, path.read_bytes()), ("0.5.2", body))
            path.unlink()
            with patch("thock.update.Account._request", return_value={**offer, "sha256": "0" * 64}):
                with self.assertRaises(ValueError):
                    update.fetch()
            self.assertEqual(list(Path(temp).iterdir()), [])  # nothing half-checked is left to run
            with patch("thock.update.Account._request", return_value={**offer, "version": "0.5.1"}):
                self.assertIsNone(update.fetch())

    def test_the_message_after_an_update_comes_once(self):
        import thock.update as update
        with tempfile.TemporaryDirectory() as temp, patch.object(update, "MARK", Path(temp) / "updated.json"), \
                patch.object(update, "FOLDER", Path(temp) / "ThockUpdate"), patch("thock.update.VERSION", "0.5.2"):
            update.MARK.write_text('{"to": "0.5.2"}', encoding="utf-8")
            self.assertEqual(update.updated(), "0.5.2")
            self.assertIsNone(update.updated())


if __name__ == "__main__":
    unittest.main()
