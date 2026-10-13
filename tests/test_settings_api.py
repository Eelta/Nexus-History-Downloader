"""Settings persistence checks using temporary files and a mocked local host."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus-dashboard"))
from fastapi.testclient import TestClient
import app as dashboard
from mo2 import MO2Manager
from wallpaper import WallpaperManager


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = self.root / "settings.json"
        self.original = {"DefaultDownloadDir": "F:\\Downloads", "MO2": {"enabled": False},
                         "Wallpaper": {"enabled": False}, "custom": {"keep": [42]},
                         "Appearance": {"accent": "gray"}}
        self.settings.write_text(json.dumps(self.original), encoding="utf-8")
        manager = MO2Manager(self.root, self.root)
        wallpapers = WallpaperManager(self.settings, manager.lock)
        for target, name, value in ((dashboard, "SETTINGS_FILE", self.settings),
                                    (dashboard, "mo2_manager", manager),
                                    (dashboard, "wallpaper_manager", wallpapers)):
            override = patch.object(target, name, value)
            override.start()
            self.addCleanup(override.stop)
        dirs = patch.object(dashboard.config, "ensure_dirs")
        dirs.start()
        self.addCleanup(dirs.stop)
        self.client = TestClient(dashboard.app)
        self.addCleanup(self.client.close)

    def saved(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    @staticmethod
    def host_applies(request, **kwargs):
        value = json.loads(request.data)["maxThreads"]
        return io.BytesIO(json.dumps({"ok": True, "maxThreads": value}).encode())

    def test_threads_apply_and_preserve_settings(self):
        with patch("urllib.request.urlopen", side_effect=self.host_applies):
            result = self.client.post("/api/takeover/threads", json={"maxThreads": 64})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"ok": True, "applied": True, "maxThreads": 64})
        self.assertEqual(self.saved(), {**self.original, "MaxThreads": 64})

    def test_offline_threads_read_persisted_value_and_default(self):
        with patch.object(dashboard.takeover_mod, "host_status", return_value={"ok": False}):
            self.assertEqual(self.client.get("/api/takeover/threads").json()["maxThreads"], 256)
            self.settings.write_text(json.dumps({**self.original, "MaxThreads": 32}))
            result = self.client.get("/api/takeover/threads").json()
            self.assertEqual(result["maxThreads"], 32)
            self.assertFalse(result["ok"])
            self.assertFalse(result["applied"])

    def test_unavailable_host_does_not_claim_or_persist_change(self):
        before = self.settings.read_bytes()
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            response = self.client.post("/api/takeover/threads", json={"maxThreads": 64})
        self.assertEqual(response.status_code, 502)
        self.assertIn("未确认应用，未保存", response.json()["detail"])
        self.assertEqual(self.settings.read_bytes(), before)

    def test_host_must_confirm_exact_threads(self):
        before = self.settings.read_bytes()
        for result in ({"ok": False, "maxThreads": 64}, {"ok": True, "maxThreads": 32}, []):
            with self.subTest(result=result), patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(result).encode())):
                response = self.client.post("/api/takeover/threads", json={"maxThreads": 64})
                self.assertEqual(response.status_code, 502)
                self.assertEqual(self.settings.read_bytes(), before)

    def test_threads_write_failure_reports_partial_application(self):
        with patch("urllib.request.urlopen", side_effect=self.host_applies), patch.object(dashboard, "atomic_json", side_effect=OSError("locked")):
            response = self.client.post("/api/takeover/threads", json={"maxThreads": 64})
        self.assertEqual(response.status_code, 500)
        self.assertIn("已应用，但保存失败", response.json()["detail"])
        self.assertEqual(self.saved(), self.original)

    def test_threads_reject_invalid_values_without_contacting_host(self):
        with patch("urllib.request.urlopen") as host:
            for value in (True, 0, 4097, 2.5, "32", None):
                with self.subTest(value=value):
                    response = self.client.post("/api/takeover/threads", json={"maxThreads": value})
                    self.assertEqual(response.status_code, 400)
            host.assert_not_called()

    def test_download_directory_preserves_drive_and_unc_roots(self):
        for value, expected in (("F:\\", "F:\\"), ("F:/Downloads/", "F:\\Downloads"),
                                ("\\\\server\\share\\", "\\\\server\\share\\")):
            with self.subTest(value=value):
                response = self.client.post("/api/download/dir", json={"dir": value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["dir"], expected)
                self.assertEqual(self.saved()["DefaultDownloadDir"], expected)
                self.assertEqual(self.saved()["custom"], self.original["custom"])

    def test_download_directory_rejects_partial_paths(self):
        before = self.settings.read_bytes()
        for value in ("F:", "F:relative", "downloads", "\\relative"):
            with self.subTest(value=value):
                self.assertEqual(self.client.post("/api/download/dir", json={"dir": value}).status_code, 400)
                self.assertEqual(self.settings.read_bytes(), before)

    def test_appearance_defaults_and_persists_without_losing_other_keys(self):
        self.assertEqual(self.client.get("/api/appearance/settings").json(), {"ok": True, "fontSize": 13})
        result = self.client.post("/api/appearance/settings", json={"fontSize": 18})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.get("/api/appearance/settings").json()["fontSize"], 18)
        self.assertEqual(self.saved(), {**self.original, "Appearance": {"accent": "gray", "fontSize": 18}})

    def test_appearance_rejects_invalid_size_and_keeps_file(self):
        before = self.settings.read_bytes()
        for value in (True, 10, 23, 12.5, "18", None):
            with self.subTest(value=value):
                self.assertEqual(self.client.post("/api/appearance/settings", json={"fontSize": value}).status_code, 400)
        self.assertEqual(self.client.post("/api/appearance/settings", json=[]).status_code, 400)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_concurrent_sections_merge_under_shared_lock(self):
        updates = (("/api/appearance/settings", {"fontSize": 20}),
                   ("/api/takeover/threads", {"maxThreads": 128}),
                   ("/api/download/dir", {"dir": "G:\\Packages"}),
                   ("/api/wallpaper/settings", {"opacity": .6}),
                   ("/api/mo2/settings", {"deleteAfterInstall": True}))
        with patch("urllib.request.urlopen", side_effect=self.host_applies), ThreadPoolExecutor(max_workers=5) as pool:
            responses = list(pool.map(lambda args: self.client.post(args[0], json=args[1]), updates))
        self.assertTrue(all(response.status_code == 200 for response in responses), [r.text for r in responses])
        data = self.saved()
        self.assertEqual(data["MaxThreads"], 128)
        self.assertEqual(data["Appearance"], {"accent": "gray", "fontSize": 20})
        self.assertEqual(data["DefaultDownloadDir"], "G:\\Packages")
        self.assertEqual(data["Wallpaper"]["opacity"], .6)
        self.assertTrue(data["MO2"]["deleteAfterInstall"])
        self.assertEqual(data["custom"], self.original["custom"])

    def test_blocking_host_request_runs_off_event_loop(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_host(request, **kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("event loop could not continue")
            return self.host_applies(request, **kwargs)

        class RequestBody:
            async def json(self):
                return {"maxThreads": 64}

        async def exercise():
            task = asyncio.create_task(dashboard.takeover_threads_set(RequestBody()))
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.01)
            self.assertTrue(entered.is_set())
            release.set()
            self.assertTrue((await task)["ok"])

        with patch("urllib.request.urlopen", side_effect=slow_host):
            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
