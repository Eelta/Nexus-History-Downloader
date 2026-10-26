"""Archive snapshots survive missing live history and frontend reloads."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus-dashboard"))
from fastapi.testclient import TestClient
import app as dashboard


class DiscardedApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.archive = Path(temp.name) / "discarded.json"
        self.mod = {
            "mod_id": 1001, "game_domain": "skyrimspecialedition",
            "name": "Archived SkyUI", "updated_at": "2026-10-01T12:00:00+00:00",
            "downloaded_at": "2026-09-01T12:00:00+00:00",
            "url": "https://www.nexusmods.com/skyrimspecialedition/mods/1001",
        }
        for target, name, value in ((dashboard.config, "DISCARD_FILE", self.archive),
                                     (dashboard.config, "SCAN_CACHE_FILE", Path(temp.name) / "scan.json"),
                                     (dashboard, "state", dashboard.AppState())):
            override = patch.object(target, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.client = TestClient(dashboard.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def toggle(self, **body):
        return self.client.post("/api/discarded/toggle", json={"mod_id": 1001, **body})

    def test_archive_survives_empty_history_and_new_app_state(self):
        dashboard.state.mods = [self.mod]
        expected = {"ids": [1001], "items": [self.mod]}
        self.assertEqual(self.toggle().json(), expected)
        dashboard.state = dashboard.AppState()
        self.assertEqual(self.client.get("/api/mods").json()["items"], [])
        self.assertEqual(self.client.get("/api/discarded").json(), expected)
        self.assertEqual(json.loads(self.archive.read_text()), expected)
        self.assertEqual(self.toggle().json(), {"ids": [], "items": []})
        self.assertEqual(self.client.get("/api/discarded").json(), {"ids": [], "items": []})

    def test_old_ids_are_migrated_and_missing_ids_remain_recoverable(self):
        self.archive.write_text(json.dumps({"ids": [1002, "1001", 1001]}))
        dashboard.state.mods = [self.mod]
        data = self.client.get("/api/discarded").json()
        self.assertEqual(data["ids"], [1001, 1002])
        self.assertEqual(data["items"][0], self.mod)
        self.assertEqual(data["items"][1]["mod_id"], 1002)
        self.assertTrue(data["items"][1]["url"].endswith("/mods/1002"))
        dashboard.state.mods = []
        self.assertEqual(self.client.get("/api/discarded").json(), data)
        self.assertEqual(json.loads(self.archive.read_text()), data)
        response = self.client.post("/api/discarded/toggle", json={"mod_id": 1002})
        self.assertEqual(response.json(), {"ids": [1001], "items": [self.mod]})

    def test_snapshot_updates_from_live_history_then_survives_its_removal(self):
        self.assertEqual(self.toggle(item=self.mod).status_code, 200)
        newer = {**self.mod, "name": "Newer name", "updated_at": "2026-10-25T00:00:00Z"}
        dashboard.state.mods = [newer]
        self.assertEqual(self.client.get("/api/discarded").json()["items"], [newer])
        dashboard.state.mods = []
        self.assertEqual(self.client.get("/api/discarded").json()["items"], [newer])

    def test_old_ids_recover_names_and_dates_from_unfiltered_cache_at_startup(self):
        self.archive.write_text(json.dumps({"ids": [1001]}))
        row = {"domain": dashboard.config.TARGET_GAME, "mod_id": 1001,
               "cells": ["Cached archive", "2026/10/25", "", "", "2026/10/01"]}
        dashboard.config.SCAN_CACHE_FILE.write_text(json.dumps({
            "app_version": "2.46", "game": dashboard.config.TARGET_GAME,
            "timezone_offset_min": -480, "items": [None, row],
        }))
        # This row is excluded from monitoring because its download is newer.
        import scraper
        with patch.object(scraper, "TZ_OFFSET_MIN", 0):
            data = self.client.get("/api/discarded").json()
        self.assertEqual(data["items"][0]["name"], "Cached archive")
        self.assertTrue(data["items"][0]["updated_at"].startswith("2026-09-30"))
        self.assertTrue(data["items"][0]["downloaded_at"].startswith("2026-10-24"))
        dashboard.config.SCAN_CACHE_FILE.unlink()
        self.assertEqual(self.client.get("/api/discarded").json(), data)

    def test_stale_frontend_can_archive_its_snapshot_after_live_scan_changes(self):
        self.assertEqual(self.toggle(item=self.mod).json(), {"ids": [1001], "items": [self.mod]})
        self.assertEqual(self.client.get("/api/discarded").json()["items"], [self.mod])

    def test_failed_load_or_save_preserves_previous_file(self):
        self.toggle(item=self.mod)
        before = self.archive.read_bytes()
        with patch.object(dashboard, "atomic_json", side_effect=OSError("locked")):
            self.assertEqual(self.toggle().status_code, 500)
        self.assertEqual(self.archive.read_bytes(), before)
        self.archive.write_text("broken JSON")
        self.assertEqual(self.client.get("/api/discarded").status_code, 500)
        self.assertEqual(self.toggle().status_code, 500)
        self.assertEqual(self.archive.read_text(), "broken JSON")

    def test_invalid_or_missing_records_do_not_create_empty_archives(self):
        for body in ({"mod_id": True}, {"mod_id": -1}, {"mod_id": 1.5},
                     {"mod_id": "1001"}, {"mod_id": 1001, "item": []},
                     {"mod_id": 1001, "item": {"mod_id": 1002}}, [], {}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/discarded/toggle", json=body).status_code, 400)
        self.assertEqual(self.toggle().status_code, 400)
        self.assertFalse(self.archive.exists())


if __name__ == "__main__":
    unittest.main()
