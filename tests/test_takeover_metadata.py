"""Offline tests for download identity and preservation of mod archive metadata."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus-dashboard"))
import config
import takeover


class DownloadMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.cache_patch = patch.object(config, "MODS_CACHE_DIR", self.cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.dirs_patch = patch.object(config, "ensure_dirs", lambda: None)
        self.dirs_patch.start()
        self.addCleanup(self.dirs_patch.stop)
        self.path = self.cache / "skyrimspecialedition-42.json"

    def test_signed_url_uses_current_mod_and_file_page(self):
        self.assertEqual(takeover._combined_info(
            "https://download.nexusmods.com/token",
            "https://www.nexusmods.com/skyrimspecialedition/mods/42?file_id=81"),
            ("skyrimspecialedition", 42, "81"))

    def test_partial_identity_survives_without_file_id(self):
        self.assertEqual(takeover._combined_info(
            "https://download.nexusmods.com/token",
            "https://www.nexusmods.com/skyrimspecialedition/mods/42"),
            ("skyrimspecialedition", 42, None))

    def test_download_identity_wins_over_unrelated_page(self):
        self.assertEqual(takeover._combined_info(
            "https://www.nexusmods.com/skyrimspecialedition/mods/42",
            "https://www.nexusmods.com/skyrimspecialedition/mods/99?file_id=81"),
            ("skyrimspecialedition", 42, None))

    def test_submit_keeps_old_cached_name_and_leaves_archive_bytes_unchanged(self):
        self.path.write_text(json.dumps({"name": "我的 MO2 模组", "app_version": "old",
                                         "archived": True}), encoding="utf-8")
        before = self.path.read_bytes()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true,"jobId":"test"}'
        with patch.object(takeover.urllib.request, "urlopen", return_value=response) as opened:
            result = takeover.submit_download(
                "https://download.nexusmods.com/token?fid=81", "update.zip", "sid=secret",
                "https://www.nexusmods.com/skyrimspecialedition/mods/42", "D:/Downloads")
        payload = json.loads(opened.call_args.args[0].data)
        self.assertTrue(result["ok"])
        self.assertEqual((payload["game"], payload["modId"], payload["fileId"], payload["modName"]),
                         ("skyrimspecialedition", 42, "81", "我的 MO2 模组"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_online_cache_merge_preserves_archive_fields_and_missing_old_files(self):
        old = {"name": "Existing name", "archived": True, "custom": {"note": "keep"},
               "app_version": "old", "files": {
                   "Old files": [{"file_id": "2", "name": "old.zip"}],
                   "Main files": [{"file_id": "81", "name": "old-name.zip", "note": "keep"}]}}
        self.path.write_text(json.dumps(old), encoding="utf-8")
        takeover._merge_download_file_cache("skyrimspecialedition", 42, {
            "Main files": [{"file_id": "81", "name": "update.zip"},
                           {"file_id": "82", "name": "extra.zip"}]})
        merged = json.loads(self.path.read_text(encoding="utf-8"))
        for key in ("name", "archived", "custom", "app_version"):
            self.assertEqual(merged[key], old[key])
        self.assertEqual(merged["files"]["Old files"], old["files"]["Old files"])
        self.assertEqual(merged["files"]["Main files"][0],
                         {"file_id": "81", "name": "update.zip", "note": "keep"})
        self.assertEqual(len(merged["files"]["Main files"]), 2)

    def test_unreadable_archive_is_never_replaced(self):
        original = b'{"archived":true, partial'
        self.path.write_bytes(original)
        with self.assertRaises(ValueError):
            takeover._merge_download_file_cache("skyrimspecialedition", 42, {})
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
