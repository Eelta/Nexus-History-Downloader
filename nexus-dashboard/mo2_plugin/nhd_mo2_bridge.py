"""MO2 GUI-thread installation bridge, compatible with Python 3.8 / Qt 5.

Deploy this file to MO2/plugins beside nhd_bridge.json. That JSON contains an
absolute queue_dir. The dashboard owns the queue and confirms install names;
only MO2 performs installation. This plugin never deletes downloaded archives
or edits MO2's mod metadata, profile, load order, or installer selections.
"""

import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time

import mobase

try:
    from PyQt6.QtCore import QLockFile, QThread, QTimer
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication, QMessageBox
except ImportError:
    from PyQt5.QtCore import QLockFile, QThread, QTimer
    from PyQt5.QtGui import QIcon
    from PyQt5.QtWidgets import QApplication, QMessageBox


LOG = logging.getLogger("NHD MO2 Bridge")
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.I)


def _read_json(path):
    # Requests/configuration are small. Never read an arbitrary archive as JSON.
    with path.open("r", encoding="utf-8-sig") as stream:
        value = stream.read(262145)
    if len(value) > 262144:
        raise ValueError("Bridge JSON exceeds 256 KiB")
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("Bridge JSON must contain an object")
    return result


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(path.parent),
            prefix=".nhd-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _same_path(left, right):
    return os.path.normcase(os.path.realpath(str(left))) == os.path.normcase(
        os.path.realpath(str(right))
    )


class NHDMO2Bridge(mobase.IPluginTool):
    def __init__(self):
        super().__init__()
        self._organizer = None
        self._window = None
        self._timer = None
        self._queue = None
        self._queue_lock = None
        self._needs_recovery = False
        self._busy = False
        self._current_id = None
        self._pending_response = None
        self._last_error = None
        # MO2 2.4.x executes plugin source without defining __file__. The
        # downloader deploys the bridge beside the executable, not inside the
        # active profile/base path; Qt provides that directory on both Qt 5/6.
        self._config_path = Path(QApplication.applicationDirPath()) / "plugins" / "nhd_bridge.json"

    def init(self, organizer):
        self._organizer = organizer
        organizer.onUserInterfaceInitialized(self._ui_ready)
        return True

    def name(self):
        return "NHD MO2 Bridge"

    def author(self):
        return "Nexus History Downloader"

    def description(self):
        return "Install confirmed Nexus History Downloader archives using MO2's own installers."

    def version(self):
        return mobase.VersionInfo(1, 0, 2)

    def isActive(self):
        return self._organizer is not None and bool(
            self._organizer.isPluginEnabled(self.name())
        )

    def settings(self):
        # MO2 2.4+ owns enabled state; an "enabled" PluginSetting is migrated
        # out of pluginSetting() and therefore reads back as None.
        return []

    def displayName(self):
        return "Nexus History Downloader 安装桥接"

    def tooltip(self):
        return "查看下载器安装队列连接状态"

    def icon(self):
        return QIcon()

    def setParentWidget(self, widget):
        # The UI-initialized callback, not this early setter, starts the timer.
        self._parent_widget = widget

    def display(self):
        try:
            if not self.isActive():
                message = "已停用"
            else:
                # Opening this status dialog never claims a request or invokes
                # an installer. Recovery is left for the normal timer tick.
                if not self._busy:
                    self._configure_queue(recover=False)
                self._heartbeat()
                self._last_error = None
                pending = sum(
                    1 for path in (self._queue / "requests").glob("*.json")
                    if REQUEST_ID.fullmatch(path.stem)
                    and not path.with_suffix(".processing").exists()
                    and not (self._queue / "responses" / path.name).exists()
                )
                status = "正在安装" if self._busy else "已连接"
                detail = ("请完成 MO2 的安装选项" if self._busy else
                          "关闭此窗口后自动安装" if pending else
                          "请在下载器确认安装名称")
                message = "{} · 待安装 {}\n\n{}".format(status, pending, detail)
        except Exception as error:
            self._report_error(error)
            message = "连接失败\n\n" + self._last_error
        QMessageBox.information(self._window, self.displayName(), message)

    def _ui_ready(self, window):
        self._window = window
        if self._timer is None:
            # A QTimer parented to the main window keeps installation on MO2's
            # GUI thread. It can fire again inside native installer event loops.
            self._timer = QTimer(window)
            self._timer.setInterval(1000)
            self._timer.timeout.connect(self._tick)
            self._timer.start()

    def _report_error(self, error):
        message = str(error)
        if message != self._last_error:
            LOG.warning("%s", message)
        self._last_error = message

    def _configure_queue(self, recover=True):
        config = _read_json(self._config_path)
        configured = config.get("queue_dir")
        if not isinstance(configured, str) or not Path(configured).is_absolute():
            raise ValueError("nhd_bridge.json queue_dir must be an absolute path")
        queue = Path(configured).resolve()
        if self._queue == queue and self._queue_lock is not None:
            if recover and self._needs_recovery:
                self._recover_interrupted()
                self._needs_recovery = False
            return True
        queue.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(queue / "mo2-bridge.lock"))
        # A long FOMOD interaction must not let a second MO2 steal this lock.
        # Qt can still remove the lock after its owning process has exited.
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise RuntimeError("Another MO2 process is already handling this installation queue")
        if self._queue_lock is not None:
            self._queue_lock.unlock()
        self._queue = queue
        self._queue_lock = lock
        self._needs_recovery = True
        (queue / "requests").mkdir(exist_ok=True)
        (queue / "responses").mkdir(exist_ok=True)
        if recover:
            self._recover_interrupted()
            self._needs_recovery = False
        return True

    def _recover_interrupted(self):
        # A durable claim is intentionally never requeued: MO2 may have written
        # files just before a crash. A player must inspect and explicitly retry.
        for claim in sorted((self._queue / "requests").glob("*.processing")):
            request_id = claim.stem
            if not REQUEST_ID.fullmatch(request_id):
                continue
            response = self._queue / "responses" / (request_id + ".json")
            if not response.exists():
                _atomic_json(response, {
                    "id": request_id, "state": "error", "mod_name": "",
                    "message": "MO2 上次安装中断，结果未知。安装包已保留；请检查模组后再手动重试。",
                    "completed_at": time.time(),
                })

    def _heartbeat(self):
        _atomic_json(self._queue / "bridge-status.json", {
            "state": "installing" if self._busy else "ready",
            "updated_at": time.time(), "version": 1, "pid": os.getpid(),
            "instance_path": str(self._organizer.basePath()),
            "mods_path": str(self._organizer.modsPath()),
            "request_id": self._current_id,
        })

    def _validate_request(self, request, request_id):
        if request.get("id") != request_id:
            raise ValueError("Request ID does not match its filename")
        archive = request.get("archive_path")
        if not isinstance(archive, str) or not Path(archive).is_absolute():
            raise ValueError("Archive path must be absolute")
        if not Path(archive).is_file():
            raise ValueError("安装包不存在：" + archive)
        archive_path = Path(archive)
        archive_stat = archive_path.lstat()
        # Path.is_junction is unavailable in MO2 2.4.4's Python 3.8.
        if archive_path.is_symlink() or getattr(archive_stat, "st_file_attributes", 0) & 0x400:
            raise ValueError("安装包不能是链接；请在下载器中重新确认")
        expected_fingerprint = request.get("archive_fingerprint")
        if expected_fingerprint is not None:
            current_fingerprint = {
                "size": archive_stat.st_size, "mtime_ns": archive_stat.st_mtime_ns,
                "ino": archive_stat.st_ino,
            }
            if expected_fingerprint != current_fingerprint:
                raise ValueError("安装包在确认后发生变化，已保留；请在下载器中重新确认")
        name = request.get("install_name")
        if (not isinstance(name, str) or not name or name != name.strip()
                or len(name) > 240 or name in (".", "..") or name.endswith(".")
                or INVALID_NAME.search(name) or RESERVED_NAME.fullmatch(name)):
            raise ValueError("安装名称不是有效的 MO2 文件夹名称")
        expected = request.get("expected_mods_path")
        if expected is not None and (
            not isinstance(expected, str) or not Path(expected).is_absolute()
            or not _same_path(expected, self._organizer.modsPath())
        ):
            raise ValueError("MO2 当前模组目录与已确认的目标不同；请在下载器重新选择 MO2 实例")
        return archive, name

    def _install(self, claim, request_id):
        result = {
            "id": request_id, "state": "error", "mod_name": "", "message": "",
        }
        try:
            self._heartbeat()
            request = _read_json(claim)
            archive, name = self._validate_request(request, request_id)
            # Do not extract archives or guess installer choices here. In MO2
            # 2.4.4 this API runs installArchive and returns an IModInterface only
            # after successful installation. FOMOD/manual/overwrite dialogs all
            # remain owned by MO2 and its installed installer plugins.
            installed = self._organizer.installMod(archive, name)
            if installed is None:
                # The API does not distinguish cancellation from every native
                # installation failure. Neither authorizes archive cleanup.
                result.update(state="cancelled", message="MO2 安装已取消或未完成，安装包已保留；请查看 MO2 提示。")
            else:
                actual_name = str(installed.name())
                installed_path = Path(str(installed.absolutePath()))
                if not actual_name or not installed_path.is_dir():
                    raise RuntimeError("MO2 未返回可验证的已安装模组，安装包已保留")
                result.update(state="installed", mod_name=actual_name, message="MO2 安装完成")
        except Exception as error:
            result["message"] = str(error)
            self._report_error(error)
        result["completed_at"] = time.time()
        self._pending_response = (
            self._queue / "responses" / (request_id + ".json"), result,
        )
        self._flush_response()

    def _flush_response(self):
        if self._pending_response is not None:
            path, result = self._pending_response
            _atomic_json(path, result)
            self._pending_response = None

    def _tick(self):
        try:
            app = QApplication.instance()
            if app is None or self._window is None or QThread.currentThread() != app.thread():
                return
            # This gate must precede any operation which can run nested Qt
            # events. A FOMOD dialog may run this timer while installMod blocks.
            if self._busy:
                self._heartbeat()
                return
            if not self.isActive():
                return
            # Persist an already completed outcome before reading new config or
            # starting another request, including after transient disk errors.
            self._flush_response()
            self._configure_queue()
            self._heartbeat()
            self._last_error = None
            if not self._window.isEnabled() or QApplication.activeModalWidget() is not None:
                return
            for request_path in sorted((self._queue / "requests").glob("*.json")):
                request_id = request_path.stem
                if not REQUEST_ID.fullmatch(request_id):
                    continue
                claim = request_path.with_suffix(".processing")
                response = self._queue / "responses" / (request_id + ".json")
                if claim.exists() or response.exists():
                    continue
                # Holding QLockFile serializes consumers, and the atomic rename
                # is durable evidence that this request has already started.
                try:
                    request_path.rename(claim)
                except FileNotFoundError:
                    continue  # The dashboard cancelled before MO2 claimed it.
                self._busy = True
                self._current_id = request_id
                try:
                    self._install(claim, request_id)
                finally:
                    self._busy = False
                    self._current_id = None
                    self._heartbeat()
                break  # At most one native installer per timer tick.
        except Exception as error:
            self._report_error(error)


def createPlugin():
    return NHDMO2Bridge()
