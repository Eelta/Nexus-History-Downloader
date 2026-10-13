"""Offline installation lifecycle checks; never reads/writes a real MO2 instance."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus-dashboard"))
from mo2 import MO2Manager, atomic_json, mods_directory, suggest_install, validate_name


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mo = self.root / "MO2"
        self.mo.mkdir()
        (self.mo / "ModOrganizer.exe").touch()
        (self.mo / "mods").mkdir()
        self.archive = self.root / "UI-123-1-0.zip"
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("Data/SkyUI_SE.esp", b"plugin")
        assets = Path(__file__).resolve().parents[1] / "nexus-dashboard"
        self.manager = MO2Manager(self.root, assets)
        atomic_json(self.root / "settings.json", {"DefaultDownloadDir": "keep-this",
                    "custom": {"nested": 42}, "MO2": {"mo2Path": str(self.mo)}})
        self.archive_info = self.root / "cache/nexus-dashboard/discarded.json"
        atomic_json(self.archive_info, {"ids": [123, 456]})
        self.old_info = self.archive_info.read_bytes()
        self.download = {"id": "T1", "state": "done", "savePath": str(self.archive),
                         "startedAt": "2026-10-13", "game": "skyrimspecialedition", "modId": 123,
                         "modName": "UI", "fileId": "888"}

    def add_mod(self, name, mod_id=123, game="SkyrimSE", files=True):
        target = self.mo / "mods" / name
        target.mkdir()
        (target / "meta.ini").write_text(
            "[General]\ngameName=%s\nmodid=%s\ninstallationFile=old.zip\n[installedFiles]\n1\\fileid=888\n"
            % (game, mod_id), encoding="utf-8")
        if files:
            (target / "SkyUI_SE.esp").write_bytes(b"plugin")
        return target

    def enqueue(self, delete=False):
        self.manager.update_settings({"deleteAfterInstall": delete})
        self.manager.tick([self.download])
        self.assertEqual(len(self.manager.jobs), 1)
        job = next(iter(self.manager.jobs.values()))
        self.assertEqual(job["state"], "awaiting_confirmation")
        self.assertFalse(list(self.manager.queue_dir(self.mo).glob("requests/*.json")))
        self.manager.confirm(job["id"], "玩家修改的名称")
        return job

    def respond(self, job, state):
        atomic_json(Path(job["queueDir"]) / "responses" / (job["attempt"] + ".json"),
                    {"id": job["attempt"], "state": state, "mod_name": "Native Name", "message": state})
        self.manager.tick([])

    def seed_old_bridge(self, queue=None):
        plugins = self.mo / "plugins"
        plugins.mkdir(exist_ok=True)
        target = plugins / "nhd_mo2_bridge.py"
        target.write_bytes(b"# previous packaged bridge\n")
        config = plugins / "nhd_bridge.json"
        atomic_json(config, {"queue_dir": str(queue or self.manager.queue_dir(self.mo)), "preserve": True})
        return target, config

    def test_suggestions_use_id_game_content_and_custom_name(self):
        self.add_mod("SkyUI - 我的中文名称")
        self.add_mod("Other game", game="Fallout4")
        result = suggest_install({"archivePath": str(self.archive), **self.download}, self.mo / "mods")
        self.assertEqual(result["suggestedName"], "SkyUI - 我的中文名称")
        self.assertIn("编号相同", result["matchReason"])
        self.assertIn("相同文件路径", result["matchReason"])
        self.assertNotIn("Other game", [c["name"] for c in result["candidates"]])

    def test_every_job_waits_for_name_and_edited_name_is_sent(self):
        job = self.enqueue()
        payload = json.loads((Path(job["queueDir"]) / "requests" / (job["attempt"] + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(payload["install_name"], "玩家修改的名称")
        self.assertEqual(payload["archive_path"], str(self.archive))
        self.assertEqual(payload["expected_mods_path"], str(self.mo / "mods"))
        with self.assertRaises(ValueError):
            self.manager.confirm(job["id"], "重复安装")

    def test_only_success_can_delete_and_state_persists(self):
        job = self.enqueue(delete=True)
        self.respond(job, "installed")
        self.assertFalse(self.archive.exists())
        self.assertTrue(job["archiveDeleted"])
        restored = MO2Manager(self.root, self.manager.assets)
        restored.tick([self.download])
        self.assertEqual(len(restored.jobs), 1)
        self.assertEqual(restored.jobs[job["id"]]["installedName"], "Native Name")
        self.assertEqual(self.archive_info.read_bytes(), self.old_info)

    def test_default_keeps_archive_and_settings_metadata(self):
        job = self.enqueue()
        self.respond(job, "installed")
        self.assertTrue(self.archive.exists())
        settings = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["custom"], {"nested": 42})
        self.assertEqual(settings["DefaultDownloadDir"], "keep-this")

    def test_cancel_and_failure_keep_archive_retry_needs_confirmation(self):
        job = self.enqueue(delete=True)
        self.respond(job, "cancelled")
        self.assertTrue(self.archive.exists())
        first_attempt = job["attempt"]
        self.manager.retry(job["id"])
        self.assertEqual(job["state"], "awaiting_confirmation")
        self.manager.confirm(job["id"], "Retry")
        self.assertNotEqual(job["attempt"], first_attempt)
        self.respond(job, "error")
        self.assertTrue(self.archive.exists())

    def test_changed_file_cannot_be_deleted(self):
        job = self.enqueue(delete=True)
        self.archive.write_bytes(b"different archive data")
        self.respond(job, "installed")
        self.assertTrue(self.archive.exists())
        self.assertIn("变化", job["cleanupError"])

    def test_skip_preserves_archive_and_is_not_requeued(self):
        self.manager.tick([self.download])
        job = next(iter(self.manager.jobs.values()))
        self.manager.skip(job["id"])
        self.manager.tick([self.download])
        self.assertEqual(job["state"], "cancelled")
        self.assertTrue(self.archive.exists())

    def test_matching_preserves_meta_bytes(self):
        target = self.add_mod("自定义名称")
        old = (target / "meta.ini").read_bytes()
        self.manager.tick([self.download])
        self.assertEqual((target / "meta.ini").read_bytes(), old)

    def test_corrupt_state_is_not_overwritten(self):
        self.manager.directory.mkdir(parents=True)
        self.manager.state_file.write_text("{bad json", encoding="utf-8")
        broken = MO2Manager(self.root, self.manager.assets)
        broken.tick([self.download])
        self.assertFalse(broken.status()["ok"])
        self.assertEqual(broken.state_file.read_text(), "{bad json")

    def test_string_false_setting_cannot_authorize_deletion(self):
        settings_path = self.root / "settings.json"
        original = {"MO2": {"mo2Path": str(self.mo), "deleteAfterInstall": "false"}}
        atomic_json(settings_path, original)
        with self.assertRaises(ValueError):
            self.manager.settings()
        self.assertEqual(json.loads(settings_path.read_text()), original)

    def test_corrupt_record_pauses_without_deletion(self):
        job = self.enqueue(delete=True)
        job["deleteAfterInstall"] = "false"
        atomic_json(self.manager.state_file, self.manager.jobs)
        broken = MO2Manager(self.root, self.manager.assets)
        broken.tick([])
        self.assertFalse(broken.status()["ok"])
        self.assertTrue(self.archive.exists())

    def test_history_partial_line_and_restart_dedup(self):
        history = self.root / "cache/takeover-history.jsonl"
        history.parent.mkdir(exist_ok=True)
        data = json.dumps(self.download).encode()
        history.write_bytes(data[:20])
        self.manager.tick([])
        self.assertFalse(self.manager.jobs)
        with history.open("ab") as f:
            f.write(data[20:] + b"\n")
        self.manager.tick([])
        self.assertEqual(len(self.manager.jobs), 1)
        restored = MO2Manager(self.root, self.manager.assets)
        restored.tick([self.download])
        self.assertEqual(len(restored.jobs), 1)

    def test_invalid_names_never_reach_native_installer(self):
        for name in ("", "..", "../other", "C:\\escape", "bad/name", "NUL", "name."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_name(name)

    def test_custom_mods_directory(self):
        custom = self.root / "custom"
        (self.mo / "ModOrganizer.ini").write_text("[Settings]\nbase_directory=" + str(custom) + "\nmod_directory=%BASE_DIR%/my_mods\n", encoding="utf-8")
        self.assertEqual(mods_directory(self.mo), custom / "my_mods")

    def test_save_failure_cannot_publish_request(self):
        self.manager.tick([self.download])
        job = next(iter(self.manager.jobs.values()))
        with patch.object(self.manager, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.manager.confirm(job["id"], "Name")
        self.assertFalse(list(self.manager.queue_dir(self.mo).glob("requests/*.json")))

    def test_instance_cannot_change_while_installing(self):
        self.enqueue()
        with self.assertRaises(ValueError):
            self.manager.update_settings({"mo2Path": str(self.root / "another")})

    def test_claim_rename_during_poll_cannot_enable_duplicate_retry(self):
        job = self.enqueue()
        request = Path(job["queueDir"]) / "requests" / (job["attempt"] + ".json")
        claim = request.with_suffix(".processing")
        original = Path.exists
        def racing_exists(path):
            if path == claim and original(request):
                # Reproduce a rename immediately after a negative claim read.
                request.rename(claim)
                return False
            if path == request and original(request):
                request.rename(claim)
                return False
            return original(path)
        with patch.object(Path, "exists", racing_exists):
            self.manager.collect_results()
        self.assertEqual(job["state"], "installing")
        with self.assertRaises(ValueError):
            self.manager.retry(job["id"])

    def test_retention_setting_defaults_validates_and_preserves_other_settings(self):
        self.assertEqual(self.manager.settings()["historyRetentionDays"], 30)
        for days in (0, 7, 30, 90):
            self.assertEqual(self.manager.update_settings({"historyRetentionDays": days})["settings"]["historyRetentionDays"], days)
        original = (self.root / "settings.json").read_bytes()
        for days in (True, False, "7", -1, 1, 7.0, None, []):
            with self.subTest(days=days), self.assertRaises(ValueError):
                self.manager.update_settings({"historyRetentionDays": days})
        self.assertEqual((self.root / "settings.json").read_bytes(), original)
        settings = json.loads(original)
        self.assertEqual(settings["custom"], {"nested": 42})
        self.assertEqual(self.archive_info.read_bytes(), self.old_info)

    def test_expired_history_is_pruned_without_touching_package_mods_or_bridge(self):
        target = self.add_mod("Existing mod")
        metadata = (target / "meta.ini").read_bytes()
        archive = self.archive.read_bytes()
        job = self.enqueue()
        self.respond(job, "installed")
        job["completedAt"] = time.time() - 31 * 86400
        self.manager.save()
        queue = Path(job["queueDir"])
        queue_files = {str(path): path.read_bytes() for path in queue.rglob("*") if path.is_file()}
        self.manager.tick([self.download])
        self.assertEqual(self.manager.jobs, {})
        self.assertIn(job["id"], self.manager.tombstones)
        self.assertEqual(self.archive.read_bytes(), archive)
        self.assertEqual((target / "meta.ini").read_bytes(), metadata)
        self.assertEqual(self.archive_info.read_bytes(), self.old_info)
        self.assertEqual({str(path): path.read_bytes() for path in queue.rglob("*") if path.is_file()}, queue_files)

    def test_pruned_jobs_stay_deduplicated_after_poll_restart_and_history_replay(self):
        self.manager.tick([self.download])
        job = next(iter(self.manager.jobs.values()))
        self.manager.skip(job["id"])
        job["completedAt"] = time.time() - 31 * 86400
        self.manager.save()
        self.manager.tick([self.download])
        self.assertFalse(self.manager.jobs)
        for name in ("takeover-jobs.jsonl", "takeover-history.jsonl"):
            (self.root / "cache" / name).write_text(json.dumps(self.download) + "\n", encoding="utf-8")
        restored = MO2Manager(self.root, self.manager.assets)
        for _ in range(3):
            restored.tick([self.download])
        self.assertFalse(restored.jobs)
        self.assertEqual(restored.tombstones, {job["id"]})
        # A new explicit download of the same package remains eligible.
        restored.tick([{**self.download, "id": "new-download"}])
        self.assertEqual(len(restored.jobs), 1)
        self.assertNotIn(job["id"], restored.jobs)

    def test_retention_keeps_active_failed_cleanup_error_and_recent_records(self):
        variants = [
            ("awaiting_confirmation", False, 365), ("queued", False, 365),
            ("installing", False, 365), ("error", False, 365),
            ("installed", True, 365), ("cancelled", True, 365),
            ("installed", False, 1), ("cancelled", False, 1),
        ]
        self.manager.tick([{**self.download, "id": str(index)} for index in range(len(variants))])
        for job, (state, cleanup_error, age) in zip(self.manager.jobs.values(), variants):
            job.update(state=state, cleanupError="needs attention" if cleanup_error else "",
                       completedAt=time.time() - age * 86400)
        self.assertFalse(self.manager.prune_history(30))
        self.assertEqual(len(self.manager.jobs), len(variants))
        self.assertFalse(self.manager.tombstone_file.exists())

    def test_zero_retention_keeps_old_history_and_shorter_setting_applies_immediately(self):
        self.manager.update_settings({"historyRetentionDays": 0})
        self.manager.tick([self.download])
        job = next(iter(self.manager.jobs.values()))
        self.manager.skip(job["id"])
        job["completedAt"] = time.time() - 10 * 86400
        self.manager.save()
        self.manager.tick([self.download])
        self.assertIn(job["id"], self.manager.jobs)
        result = self.manager.update_settings({"historyRetentionDays": 7})
        self.assertEqual(result["jobs"], [])
        self.manager.update_settings({"historyRetentionDays": 0})
        self.manager.tick([self.download])
        self.assertEqual(self.manager.jobs, {})

    def test_legacy_terminal_record_gets_full_retention_period_and_time_persists(self):
        self.manager.tick([self.download])
        job = next(iter(self.manager.jobs.values()))
        job.update(state="installed")
        self.manager.save()
        with patch("mo2.time.time", return_value=1000000000):
            self.manager.tick([])
        self.assertEqual(job["completedAt"], 1000000000)
        restored = MO2Manager(self.root, self.manager.assets)
        with patch("mo2.time.time", return_value=1000000000 + 29 * 86400):
            restored.tick([self.download])
        self.assertIn(job["id"], restored.jobs)
        with patch("mo2.time.time", return_value=1000000000 + 31 * 86400):
            restored.tick([self.download])
        self.assertNotIn(job["id"], restored.jobs)

    def test_tombstone_write_failure_keeps_history_and_old_dedup_record(self):
        self.manager.tick([self.download])
        job = next(iter(self.manager.jobs.values()))
        self.manager.skip(job["id"])
        job["completedAt"] = time.time() - 31 * 86400
        self.manager.save()
        old = self.manager.state_file.read_bytes()
        real_write = atomic_json
        def fail_tombstone(path, value):
            if path == self.manager.tombstone_file:
                raise OSError("disk full")
            real_write(path, value)
        with patch("mo2.atomic_json", side_effect=fail_tombstone), self.assertRaises(OSError):
            self.manager.tick([self.download])
        self.assertIn(job["id"], self.manager.jobs)
        self.assertFalse(self.manager.tombstones)
        self.assertEqual(self.manager.state_file.read_bytes(), old)

    def test_job_write_failure_after_pruning_still_keeps_durable_tombstone(self):
        self.manager.tick([self.download])
        job = next(iter(self.manager.jobs.values()))
        self.manager.skip(job["id"])
        job["completedAt"] = time.time() - 31 * 86400
        self.manager.save()
        with patch.object(self.manager, "save", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.manager.tick([self.download])
        self.assertIn(job["id"], json.loads(self.manager.tombstone_file.read_text())["ids"])
        restored = MO2Manager(self.root, self.manager.assets)
        restored.tick([self.download])
        self.assertFalse(restored.jobs)
        self.assertTrue(self.archive.exists())

    def test_corrupt_tombstones_pause_instead_of_recreating_old_downloads(self):
        self.manager.directory.mkdir(parents=True)
        self.manager.tombstone_file.write_text("{bad json", encoding="utf-8")
        broken = MO2Manager(self.root, self.manager.assets)
        broken.tick([self.download])
        self.assertFalse(broken.status()["ok"])
        self.assertFalse(broken.jobs)
        self.assertEqual(broken.tombstone_file.read_text(), "{bad json")
        self.assertFalse(broken.state_file.exists())

    def test_native_completion_timestamp_and_retry_reset(self):
        job = self.enqueue()
        completed = time.time() - 60
        atomic_json(Path(job["queueDir"]) / "responses" / (job["attempt"] + ".json"),
                    {"id": job["attempt"], "state": "cancelled", "completed_at": completed})
        self.manager.tick([])
        self.assertEqual(job["completedAt"], completed)
        self.manager.retry(job["id"])
        self.assertNotIn("completedAt", job)

    def test_existing_own_bridge_upgrades_on_tick_without_queue_or_config_changes(self):
        target, config = self.seed_old_bridge()
        original_config = config.read_bytes()
        restored = MO2Manager(self.root, self.manager.assets)
        self.assertEqual(target.read_bytes(), b"# previous packaged bridge\n")
        self.assertFalse(restored.queue_dir(self.mo).exists())
        restored.tick([])
        self.assertEqual(target.read_bytes(), (restored.assets / "mo2_plugin" / target.name).read_bytes())
        self.assertEqual(config.read_bytes(), original_config)
        self.assertFalse(restored.queue_dir(self.mo).exists())
        self.assertFalse(restored.state_file.exists())
        self.assertEqual(restored.jobs, {})
        self.assertEqual(self.archive_info.read_bytes(), self.old_info)

    def test_foreign_bridge_queue_is_never_taken_over(self):
        target, config = self.seed_old_bridge(self.root / "other-downloader" / "queue")
        original_plugin, original_config = target.read_bytes(), config.read_bytes()
        self.manager.tick([])
        self.assertEqual(target.read_bytes(), original_plugin)
        self.assertEqual(config.read_bytes(), original_config)
        self.assertFalse(self.manager.queue_dir(self.mo).exists())
        self.assertFalse(self.manager.state_file.exists())

    def test_disabled_monitor_does_not_upgrade_bridge(self):
        target, config = self.seed_old_bridge()
        original_plugin, original_config = target.read_bytes(), config.read_bytes()
        self.manager.update_settings({"enabled": False})
        self.manager.tick([])
        self.assertEqual(target.read_bytes(), original_plugin)
        self.assertEqual(config.read_bytes(), original_config)
        self.assertEqual(self.manager._bridge_checks, set())

    def test_bridge_auto_upgrade_is_checked_once_per_path(self):
        self.seed_old_bridge()
        with patch.object(self.manager, "_write_bridge_plugin", wraps=self.manager._write_bridge_plugin) as write:
            self.manager.tick([])
            self.manager.tick([])
            self.manager.tick([])
        self.assertEqual(write.call_count, 1)

    def test_missing_bridge_config_or_plugin_is_not_auto_created(self):
        target, config = self.seed_old_bridge()
        config.unlink()
        self.manager.tick([])
        self.assertEqual(target.read_bytes(), b"# previous packaged bridge\n")
        self.assertFalse(config.exists())
        target.unlink()
        restored = MO2Manager(self.root, self.manager.assets)
        restored.tick([])
        self.assertFalse(target.exists())
        self.assertFalse(config.exists())

    def test_upgrade_failure_preserves_history_and_explicit_save_clears_error(self):
        target, config = self.seed_old_bridge()
        original_plugin, original_config = target.read_bytes(), config.read_bytes()
        with patch.object(self.manager, "_write_bridge_plugin", side_effect=PermissionError("file locked")):
            self.manager.tick([self.download])
        status = self.manager.status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["bridge"]["state"], "update_failed")
        self.assertIn("file locked", status["bridge"]["message"])
        self.assertEqual(len(status["jobs"]), 1)
        self.assertEqual(target.read_bytes(), original_plugin)
        self.assertEqual(config.read_bytes(), original_config)
        result = self.manager.update_settings({"historyRetentionDays": 7})
        self.assertNotEqual(result["bridge"]["state"], "update_failed")
        self.assertEqual(self.manager._bridge_update_errors, {})

    def test_confirmation_success_clears_stale_bridge_upgrade_error(self):
        self.seed_old_bridge()
        with patch.object(self.manager, "_write_bridge_plugin", side_effect=PermissionError("file locked")):
            self.manager.tick([self.download])
        job = next(iter(self.manager.jobs.values()))
        result = self.manager.confirm(job["id"], "Confirmed name")
        self.assertNotEqual(result["bridge"]["state"], "update_failed")
        self.assertEqual(self.manager._bridge_update_errors, {})
        self.assertEqual(job["state"], "queued")


if __name__ == "__main__":
    unittest.main()
