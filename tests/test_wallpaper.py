"""Wallpaper checks use temporary files only, never a real wallpaper folder."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus-dashboard"))
from mo2 import atomic_json
from wallpaper import DEFAULT_DIRECTORY, WallpaperManager


class WallpaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.images = self.root / "图片"
        self.images.mkdir()
        self.settings_file = self.root / "settings.json"
        self.shared_lock = threading.RLock()
        self.manager = WallpaperManager(self.settings_file, self.shared_lock)

    def configure(self, **values):
        return self.manager.update_settings({"directory": str(self.images), **values})

    def picture(self, name="sample.jpg"):
        path = self.images / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-image")
        return path

    def test_defaults_are_read_only(self):
        self.assertEqual(self.manager.settings(), {
            "ok": True, "enabled": True, "directory": DEFAULT_DIRECTORY,
            "opacity": 0.22, "intervalSeconds": 300})
        self.assertFalse(self.settings_file.exists())
        self.assertIs(self.manager.settings_lock, self.shared_lock)

    def test_settings_preserve_unrelated_sections_and_archived_records(self):
        original = {"MO2": {"enabled": True, "deleteAfterInstall": False},
                    "DefaultDownloadDir": "custom", "custom": [1, 2],
                    "Wallpaper": {"futureOption": 42}}
        atomic_json(self.settings_file, original)
        archived = self.root / "cache/nexus-dashboard/discarded.json"
        atomic_json(archived, {"ids": [123, 456]})
        original_archive = archived.read_bytes()
        result = self.configure(opacity=0.45)
        saved = json.loads(self.settings_file.read_text(encoding="utf-8"))
        self.assertEqual(result["opacity"], 0.45)
        for key in ("MO2", "DefaultDownloadDir", "custom"):
            self.assertEqual(saved[key], original[key])
        self.assertEqual(saved["Wallpaper"]["futureOption"], 42)
        self.assertEqual(archived.read_bytes(), original_archive)

    def test_validation_never_rewrites_failed_settings(self):
        self.configure()
        original = self.settings_file.read_bytes()
        cases = [{"enabled": "false"}, {"opacity": True}, {"opacity": "0.5"},
                 {"opacity": -1}, {"opacity": 1.001}, {"opacity": float("nan")},
                 {"opacity": float("inf")}, {"directory": "relative/path"},
                 {"directory": ""}, {"directory": 12},
                 {"directory": str(self.root / "missing")}, []]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.manager.update_settings(body)
            self.assertEqual(self.settings_file.read_bytes(), original)

    def test_corrupt_settings_are_preserved(self):
        for contents in ("{bad-json", "[]", '{"Wallpaper":[]}',
                         '{"Wallpaper":{"enabled":"false"}}'):
            self.settings_file.write_text(contents, encoding="utf-8")
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                self.configure()
            self.assertEqual(self.settings_file.read_text(encoding="utf-8"), contents)

    def test_disabled_missing_folder_is_allowed(self):
        result = self.configure(enabled=False, directory=str(self.root / "missing"))
        self.assertFalse(result["enabled"])
        self.assertFalse(self.manager.next_image()["ok"])

    def test_supported_images_recurse_without_exposing_paths(self):
        self.picture("nested/中文.JPG")
        self.picture("private.txt")
        self.configure()
        result = self.manager.next_image()
        self.assertTrue(result["ok"])
        self.assertEqual(result["filename"], "中文.JPG")
        self.assertNotIn(str(self.images), result["url"])
        self.assertEqual(self.manager.image_path(result["token"]), self.images / "nested/中文.JPG")
        self.assertEqual(len(self.manager._images), 1)
        for bad_token in ("../../private.txt", str(self.images / "private.txt"), "unknown"):
            with self.assertRaises(FileNotFoundError):
                self.manager.image_path(bad_token)

    def test_random_selection_avoids_immediate_repeat_and_allows_single_file(self):
        self.picture("first.png")
        self.configure()
        first = self.manager.next_image()
        self.assertEqual(self.manager.next_image(first["token"])["token"], first["token"])
        self.picture("second.webp")
        with patch("wallpaper.time.monotonic", return_value=self.manager._scanned_at + 301):
            second = self.manager.next_image(first["token"])
        self.assertNotEqual(second["token"], first["token"])
        third = self.manager.next_image(second["token"])
        self.assertEqual(third["token"], first["token"])

    def test_folder_change_and_disabling_invalidate_image_urls(self):
        self.picture()
        self.configure()
        result = self.manager.next_image()
        other = self.root / "other"
        other.mkdir()
        self.configure(directory=str(other))
        with self.assertRaises(FileNotFoundError):
            self.manager.image_path(result["token"])
        self.configure()
        fresh = self.manager.next_image()
        self.assertNotEqual(fresh["token"], result["token"])
        self.configure(enabled=False)
        with self.assertRaises(FileNotFoundError):
            self.manager.image_path(fresh["token"])

    def test_missing_empty_and_deleted_images_are_graceful(self):
        self.configure()
        self.assertFalse(self.manager.next_image()["ok"])
        path = self.picture()
        with patch("wallpaper.time.monotonic", return_value=self.manager._scanned_at + 301):
            result = self.manager.next_image()
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.manager.image_path(result["token"])
        self.assertFalse(self.manager.next_image()["ok"])
        self.images.rmdir()
        self.assertFalse(self.manager.next_image()["ok"])

    def test_inventory_caches_and_refreshes_after_five_minutes(self):
        self.picture()
        self.configure()
        with patch("wallpaper.os.walk", wraps=os.walk) as walker:
            self.manager.next_image()
            self.manager.next_image()
            self.assertEqual(walker.call_count, 1)
            with patch("wallpaper.time.monotonic", return_value=self.manager._scanned_at + 301):
                self.manager.next_image()
            self.assertEqual(walker.call_count, 2)

    def test_outside_image_never_passes_containment_validation(self):
        outside = self.root / "outside.jpg"
        outside.write_bytes(b"private")
        self.assertIsNone(self.manager._safe_file(outside, self.images))

    def test_symlinks_outside_folder_and_cycles_are_skipped(self):
        outside = self.root / "private"
        outside.mkdir()
        (outside / "private.jpg").write_bytes(b"private")
        try:
            (self.images / "escape").symlink_to(outside, target_is_directory=True)
            (self.images / "cycle").symlink_to(self.images, target_is_directory=True)
            (self.images / "linked.jpg").symlink_to(outside / "private.jpg")
        except OSError as exc:
            self.skipTest("symbolic links unavailable: " + str(exc))
        self.picture("safe.png")
        self.configure()
        result = self.manager.next_image()
        self.assertEqual(result["filename"], "safe.png")
        self.assertEqual(len(self.manager._images), 1)

    def test_cached_image_replaced_by_link_is_rejected(self):
        path = self.picture()
        outside = self.root / "outside.jpg"
        outside.write_bytes(b"private")
        self.configure()
        result = self.manager.next_image()
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError as exc:
            self.skipTest("symbolic links unavailable: " + str(exc))
        with self.assertRaises(FileNotFoundError):
            self.manager.image_path(result["token"])
        self.assertFalse(self.manager.next_image()["ok"])

    @unittest.skipUnless(os.name == "nt", "Windows junction check")
    def test_windows_junction_is_skipped_and_cached_directory_swap_is_rejected(self):
        outside = self.root / "private"
        outside.mkdir()
        (outside / "inside.jpg").write_bytes(b"private")
        directory = self.images / "nested"
        self.picture("nested/inside.jpg")
        self.configure()
        result = self.manager.next_image()
        (directory / "inside.jpg").unlink()
        directory.rmdir()
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(directory), str(outside)],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(directory.is_junction())
        with self.assertRaises(FileNotFoundError):
            self.manager.image_path(result["token"])
        with patch("wallpaper.time.monotonic", return_value=self.manager._scanned_at + 301):
            self.assertFalse(self.manager.next_image()["ok"])
        self.assertEqual((outside / "inside.jpg").read_bytes(), b"private")


if __name__ == "__main__":
    unittest.main()
