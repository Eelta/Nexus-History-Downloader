"""Wallpaper HTTP boundary checks with a temporary folder and settings file."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus-dashboard"))
from fastapi.testclient import TestClient
import app as dashboard
from wallpaper import WallpaperManager


class WallpaperApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.images = self.root / "wallpapers"
        self.images.mkdir()
        self.image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3ioAAAAASUVORK5CYII=")
        (self.images / "one.png").write_bytes(self.image)
        (self.images / "two.png").write_bytes(self.image)
        self.settings = self.root / "settings.json"
        self.settings.write_text(json.dumps({"DefaultDownloadDir": "unchanged", "MO2": {"enabled": False}}))
        manager = WallpaperManager(self.settings)
        manager.update_settings({"directory": str(self.images)})
        self.override = patch.object(dashboard, "wallpaper_manager", manager)
        self.override.start()
        self.addCleanup(self.override.stop)
        self.client = TestClient(dashboard.app)
        self.addCleanup(self.client.close)

    def test_settings_random_and_image_response(self):
        settings = self.client.get("/api/wallpaper/settings").json()
        self.assertEqual(settings["intervalSeconds"], 300)
        first = self.client.get("/api/wallpaper/next").json()
        second = self.client.get("/api/wallpaper/next", params={"exclude": first["token"]}).json()
        self.assertNotEqual(first["token"], second["token"])
        response = self.client.get(first["url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.image)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_save_preserves_sections_and_disable_revokes_image(self):
        first = self.client.get("/api/wallpaper/next").json()
        response = self.client.post("/api/wallpaper/settings", json={"enabled": False, "opacity": .6})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["opacity"], .6)
        self.assertEqual(self.client.get(first["url"]).status_code, 404)
        saved = json.loads(self.settings.read_text())
        self.assertEqual(saved["MO2"], {"enabled": False})
        self.assertEqual(saved["DefaultDownloadDir"], "unchanged")

    def test_invalid_settings_and_unissued_token(self):
        original = self.settings.read_bytes()
        self.assertEqual(self.client.post("/api/wallpaper/settings", json={"opacity": 999}).status_code, 400)
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertEqual(self.client.get("/api/wallpaper/image/not-issued").status_code, 404)


if __name__ == "__main__":
    unittest.main()
