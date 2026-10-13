"""Protocol/installer-boundary tests; no real MO2 process or user mods touched."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch


class FakeApplication:
    modal = None
    gui_thread = object()
    application_dir = ""

    @classmethod
    def applicationDirPath(cls):
        return cls.application_dir

    @classmethod
    def instance(cls):
        return cls()

    @classmethod
    def activeModalWidget(cls):
        return cls.modal

    def thread(self):
        return self.gui_thread


class FakeLock:
    held = {}

    def __init__(self, path):
        self.path = path

    def setStaleLockTime(self, value):
        assert value == 0

    def tryLock(self, timeout):
        if self.path in self.held:
            return False
        self.held[self.path] = self
        return True

    def unlock(self):
        if self.held.get(self.path) is self:
            self.held.pop(self.path)


class FakeTimer:
    def __init__(self, parent):
        self.parent = parent
        self.timeout = types.SimpleNamespace(connect=lambda callback: None)

    def setInterval(self, interval):
        self.interval = interval

    def start(self):
        pass


def load_bridge(embedded=False, qt_major=5):
    mobase = types.ModuleType("mobase")
    mobase.IPluginTool = type("IPluginTool", (), {})
    mobase.VersionInfo = lambda *args: args
    mobase.PluginSetting = lambda *args: args
    qt_name = "PyQt{}".format(qt_major)
    core = types.ModuleType(qt_name + ".QtCore")
    core.QLockFile = FakeLock
    core.QTimer = FakeTimer
    core.QThread = types.SimpleNamespace(currentThread=lambda: FakeApplication.gui_thread)
    gui = types.ModuleType(qt_name + ".QtGui")
    gui.QIcon = lambda: None
    widgets = types.ModuleType(qt_name + ".QtWidgets")
    widgets.QApplication = FakeApplication
    widgets.QMessageBox = types.SimpleNamespace(information=lambda *args: None)
    with patch.dict("sys.modules", {
        "mobase": mobase, "PyQt5": None, "PyQt6": None,
        qt_name: types.ModuleType(qt_name),
        qt_name + ".QtCore": core, qt_name + ".QtGui": gui, qt_name + ".QtWidgets": widgets,
    }):
        source = Path(__file__).resolve().parents[1] / "nhd_mo2_bridge.py"
        if embedded:
            # MO2 2.4.x's Python runner executes a script rather than importing
            # a normal module, so the script globals do not contain __file__.
            module = types.ModuleType("nhd_bridge_embedded")
            exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), module.__dict__)
            return module
        spec = importlib.util.spec_from_file_location("nhd_bridge_under_test", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


BRIDGE = load_bridge()


class FakeOrganizer:
    def __init__(self, root):
        self.root = root
        self.mods = root / "mods"
        self.mods.mkdir(exist_ok=True)
        self.enabled = True
        self.calls = []
        self.installer = self.successful_install

    def onUserInterfaceInitialized(self, callback):
        self.ui_callback = callback

    def pluginSetting(self, plugin, key):
        return self.enabled

    def basePath(self):
        return str(self.root)

    def modsPath(self):
        return str(self.mods)

    def successful_install(self, archive, name):
        destination = self.mods / name
        destination.mkdir(exist_ok=True)
        return types.SimpleNamespace(name=lambda: name, absolutePath=lambda: str(destination))

    def installMod(self, archive, name):
        self.calls.append((archive, name))
        return self.installer(archive, name)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="nhd-bridge-test-")
        self.root = Path(self.scratch.name)
        self.queue = self.root / "queue"
        self.requests = self.queue / "requests"
        self.requests.mkdir(parents=True)
        (self.queue / "responses").mkdir()
        self.archive = self.root / "A mod.7z"
        self.archive.write_bytes(b"downloaded archive kept intact")
        self.application_dir = self.root / "MO2 application"
        self.config = self.application_dir / "plugins" / "nhd_bridge.json"
        BRIDGE._atomic_json(self.config, {"queue_dir": str(self.queue)})
        FakeApplication.modal = None
        FakeApplication.application_dir = str(self.application_dir)
        FakeLock.held = {}
        self.organizer = FakeOrganizer(self.root)
        self.window = types.SimpleNamespace(isEnabled=lambda: True)
        self.plugin = self.make_plugin()

    def tearDown(self):
        FakeLock.held = {}
        self.scratch.cleanup()

    def make_plugin(self, ready=True):
        plugin = BRIDGE.createPlugin()
        plugin.init(self.organizer)
        if ready:
            self.organizer.ui_callback(self.window)
        return plugin

    def enqueue(self, request_id="one", **changes):
        payload = {
            "id": request_id, "archive_path": str(self.archive),
            "install_name": "已安装模组名称", "mod_id": 123, "file_id": 456,
            "game": "skyrimspecialedition", "expected_mods_path": str(self.organizer.mods),
        }
        payload.update(changes)
        BRIDGE._atomic_json(self.requests / (request_id + ".json"), payload)
        return payload

    def response(self, request_id="one"):
        return BRIDGE._read_json(self.queue / "responses" / (request_id + ".json"))

    def test_embedded_load_without_file_uses_executable_plugins_for_qt5_and_qt6(self):
        unrelated_cwd = self.root / "unrelated working directory"
        unrelated_cwd.mkdir()
        decoy_queue = self.root / "wrong-queue"
        # Both plausible but wrong roots have valid decoy configurations. The
        # bridge must never read either one when MO2 uses a separate instance.
        for directory in (unrelated_cwd, self.organizer.root):
            BRIDGE._atomic_json(directory / "plugins" / "nhd_bridge.json", {
                "queue_dir": str(decoy_queue),
            })
        original_cwd = Path.cwd()
        try:
            os.chdir(unrelated_cwd)
            for qt_major in (5, 6):
                with self.subTest(qt_major=qt_major):
                    module = load_bridge(embedded=True, qt_major=qt_major)
                    self.assertNotIn("__file__", module.__dict__)
                    plugin = module.createPlugin()
                    self.assertEqual(plugin._config_path, self.config)
                    self.assertTrue(plugin.init(self.organizer))
                    self.organizer.ui_callback(self.window)
                    request_id = "qt{}".format(qt_major)
                    self.enqueue(request_id)
                    plugin._tick()
                    self.assertEqual(self.response(request_id)["state"], "installed")
                    self.assertEqual(plugin._queue, self.queue)
                    self.assertEqual(module._read_json(self.queue / "bridge-status.json")["state"], "ready")
                    plugin._queue_lock.unlock()
                    self.assertFalse(decoy_queue.exists())
        finally:
            os.chdir(original_cwd)

    def test_confirmed_unicode_name_native_success_and_no_archive_cleanup(self):
        self.enqueue()
        existing = self.organizer.mods / "已安装模组名称"
        existing.mkdir()
        metadata = existing / "meta.ini"
        metadata.write_bytes(b"[General]\nnotes=keep my notes\n")
        self.plugin._tick()
        self.assertEqual(self.response()["state"], "installed")
        self.assertEqual(self.response()["mod_name"], "已安装模组名称")
        self.assertEqual(self.organizer.calls, [(str(self.archive), "已安装模组名称")])
        self.assertEqual(self.archive.read_bytes(), b"downloaded archive kept intact")
        self.assertEqual(metadata.read_bytes(), b"[General]\nnotes=keep my notes\n")
        self.assertTrue((self.requests / "one.processing").exists())
        self.assertFalse((self.requests / "one.json").exists())

    def test_native_user_rename_is_reported(self):
        self.enqueue()
        self.organizer.installer = lambda archive, name: self.organizer.successful_install(archive, "玩家在 MO2 中修改名称")
        self.plugin._tick()
        self.assertEqual(self.response()["mod_name"], "玩家在 MO2 中修改名称")

    def test_native_cancel_retains_archive_and_never_reports_success(self):
        self.enqueue()
        self.organizer.installer = lambda *args: None
        self.plugin._tick()
        self.assertEqual(self.response()["state"], "cancelled")
        self.assertTrue(self.archive.exists())

    def test_exception_retains_archive(self):
        self.enqueue()
        def broken(*args):
            raise RuntimeError("Native installation failed")
        self.organizer.installer = broken
        self.plugin._tick()
        self.assertEqual(self.response()["state"], "error")
        self.assertIn("Native installation failed", self.response()["message"])
        self.assertTrue(self.archive.exists())

    def test_nested_installer_event_loop_cannot_claim_second_archive(self):
        self.enqueue("one")
        self.enqueue("two")
        def nested(archive, name):
            self.assertTrue(self.plugin._busy)
            self.plugin._tick()
            self.assertEqual(len(self.organizer.calls), 1)
            self.assertTrue((self.requests / "two.json").exists())
            heartbeat = BRIDGE._read_json(self.queue / "bridge-status.json")
            self.assertEqual(heartbeat["state"], "installing")
            return self.organizer.successful_install(archive, name)
        self.organizer.installer = nested
        self.plugin._tick()
        self.assertEqual(len(self.organizer.calls), 1)
        self.organizer.installer = self.organizer.successful_install
        self.plugin._tick()
        self.assertEqual(len(self.organizer.calls), 2)

    def test_crash_claim_recovered_as_unknown_never_installed_again(self):
        self.enqueue()
        (self.requests / "one.json").rename(self.requests / "one.processing")
        self.plugin._tick()
        self.assertEqual(self.response()["state"], "error")
        self.assertIn("结果未知", self.response()["message"])
        self.assertEqual(self.organizer.calls, [])
        self.assertTrue(self.archive.exists())

    def test_recreated_request_with_same_id_is_not_replayed(self):
        self.enqueue()
        self.plugin._tick()
        self.enqueue()
        self.plugin._tick()
        self.assertEqual(len(self.organizer.calls), 1)

    def test_second_mo2_consumer_cannot_process_queue(self):
        self.plugin._tick()
        second = self.make_plugin()
        self.enqueue()
        second._tick()
        self.assertEqual(self.organizer.calls, [])
        self.plugin._tick()
        self.assertEqual(len(self.organizer.calls), 1)

    def test_waits_for_ui_and_modal_to_close(self):
        unready = self.make_plugin(ready=False)
        self.enqueue()
        unready._tick()
        self.assertEqual(self.organizer.calls, [])
        FakeApplication.modal = object()
        self.plugin._tick()
        self.assertEqual(self.organizer.calls, [])
        FakeApplication.modal = None
        self.plugin._tick()
        self.assertEqual(len(self.organizer.calls), 1)

    def test_disabled_plugin_and_disabled_window_do_not_claim(self):
        self.enqueue()
        self.organizer.enabled = False
        self.plugin._tick()
        self.organizer.enabled = True
        self.window.isEnabled = lambda: False
        self.plugin._tick()
        self.assertEqual(self.organizer.calls, [])
        self.assertTrue((self.requests / "one.json").exists())

    def test_wrong_mods_instance_is_refused(self):
        self.enqueue(expected_mods_path=str(self.root / "different-mods"))
        self.plugin._tick()
        self.assertEqual(self.response()["state"], "error")
        self.assertEqual(self.organizer.calls, [])

    def test_changed_archive_after_confirmation_is_not_installed(self):
        stat = self.archive.stat()
        self.enqueue(archive_fingerprint={
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ino": stat.st_ino,
        })
        self.archive.write_bytes(b"a replacement archive")
        self.plugin._tick()
        self.assertEqual(self.response()["state"], "error")
        self.assertEqual(self.organizer.calls, [])
        self.assertTrue(self.archive.exists())

    def test_unchanged_archive_fingerprint_is_accepted(self):
        stat = self.archive.stat()
        self.enqueue(archive_fingerprint={
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ino": stat.st_ino,
        })
        self.plugin._tick()
        self.assertEqual(self.response()["state"], "installed")

    def test_reparse_point_is_refused_on_python_38(self):
        self.enqueue()
        stat = self.archive.stat()
        reparse = types.SimpleNamespace(
            st_file_attributes=0x400, st_size=stat.st_size,
            st_mtime_ns=stat.st_mtime_ns, st_ino=stat.st_ino,
        )
        original_lstat = Path.lstat
        def lstat(path, *args, **kwargs):
            return reparse if path == self.archive else original_lstat(path, *args, **kwargs)
        with patch.object(Path, "lstat", lstat):
            self.plugin._tick()
        self.assertEqual(self.response()["state"], "error")
        self.assertEqual(self.organizer.calls, [])

    def test_invalid_name_and_missing_archive_are_refused(self):
        self.enqueue("bad_name", install_name="../escape")
        self.enqueue("missing", archive_path=str(self.root / "missing.7z"))
        self.plugin._tick()
        self.plugin._tick()
        self.assertEqual(self.response("bad_name")["state"], "error")
        self.assertEqual(self.response("missing")["state"], "error")
        self.assertEqual(self.organizer.calls, [])

    def test_non_gui_thread_does_not_process(self):
        self.enqueue()
        with patch.object(BRIDGE.QThread, "currentThread", return_value=object()):
            self.plugin._tick()
        self.assertEqual(self.organizer.calls, [])

    def test_success_reply_write_failure_retries_reply_without_installing_twice(self):
        self.enqueue()
        real_write = BRIDGE._atomic_json
        def fail_reply(path, value):
            if path.parent.name == "responses":
                raise OSError("temporary disk error")
            return real_write(path, value)
        with patch.object(BRIDGE, "_atomic_json", side_effect=fail_reply):
            self.plugin._tick()
        self.assertIsNotNone(self.plugin._pending_response)
        self.assertEqual(len(self.organizer.calls), 1)
        self.plugin._tick()
        self.assertEqual(self.response()["state"], "installed")
        self.assertEqual(len(self.organizer.calls), 1)

    def test_existing_success_reply_survives_plugin_restart(self):
        self.enqueue()
        self.plugin._tick()
        self.plugin._queue_lock.unlock()
        restarted = self.make_plugin()
        restarted._tick()
        self.assertEqual(self.response()["state"], "installed")
        self.assertEqual(len(self.organizer.calls), 1)


if __name__ == "__main__":
    unittest.main()
