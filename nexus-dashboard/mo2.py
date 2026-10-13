"""Persistent, confirmation-first handoff to MO2's native installer.

Only the bridge owns installation. This module reads existing mod metadata and
keeps its queue separate from Nexus history/discarded records.
"""
from __future__ import annotations

import configparser
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile

DEFAULT_MO2_PATH = r"F:\Skyrim\iniRePather-Skyrim"
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".fomod"}
HISTORY_RETENTION_DAYS = {0, 7, 30, 90}
TERMINAL_STATES = {"installed", "cancelled", "error"}


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def valid_timestamp(value) -> bool:
    return (type(value) in (int, float) and math.isfinite(value) and value > 0)


def read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, configparser.Error):
        pass
    return parser


def normalize_game(value) -> str:
    value = re.sub(r"[^a-z0-9]", "", str(value or "").casefold())
    return {"skyrimse": "skyrimspecialedition"}.get(value, value)


def normalized_name(value) -> str:
    value = Path(str(value or "").replace("\\", "/")).name
    if Path(value).suffix.lower() in ARCHIVE_EXTENSIONS:
        value = Path(value).stem
    value = re.sub(r"-\d{1,9}-.*$", "", value)
    return re.sub(r"[\W_]+", "", value.casefold())


def validate_name(value: str) -> str:
    name = str(value).strip()
    if (not name or len(name) > 180 or name in (".", "..")
            or any(c in name for c in '<>:"/\\|?*')
            or any(ord(c) < 32 for c in name) or name.endswith((".", " "))
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", name)):
        raise ValueError("安装名称必须是有效的文件夹名称（不能包含路径或特殊字符）")
    return name


def mods_directory(root: Path) -> Path:
    ini = read_ini(root / "ModOrganizer.ini")
    values = dict(ini.items("Settings")) if ini.has_section("Settings") else {}
    def expand(value, base):
        value = value.strip('"').replace("\\\\", "\\")
        value = value.replace("%BASE_DIR%", str(base)).replace("%INSTANCE_DIR%", str(root))
        result = Path(os.path.expandvars(value))
        return (result if result.is_absolute() else root / result).resolve()
    base = expand(values.get("base_directory", str(root)), root)
    return expand(values.get("mod_directory", str(base / "mods")), base)


def installed_mods(mods_path: Path) -> list[dict]:
    if not mods_path.is_dir():
        return []
    result = []
    for directory in mods_path.iterdir():
        if not directory.is_dir() or directory.name.endswith("_separator"):
            continue
        ini = read_ini(directory / "meta.ini")
        data = dict(ini.items("General")) if ini.has_section("General") else {}
        files = dict(ini.items("installedFiles")) if ini.has_section("installedFiles") else {}
        result.append({"name": directory.name, "path": directory,
                       "modId": data.get("modid", "0"),
                       "game": normalize_game(data.get("gamename")),
                       "archive": Path(data.get("installationfile", "").replace("\\", "/")).name,
                       "fileIds": {v for k, v in files.items() if k.lower().endswith("\\fileid")}})
    return result


def archive_members(path: Path) -> list[str]:
    """Read file lists only; never extract an untrusted archive ourselves."""
    names = []
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
        else:
            seven = shutil.which("7z")
            if not seven:
                for directory in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
                    candidate = Path(directory or "__missing__") / "7-Zip" / "7z.exe"
                    if candidate.is_file():
                        seven = str(candidate)
                        break
            if seven:
                proc = subprocess.run([seven, "l", "-slt", "-ba", "-sccUTF-8", "--", str(path)],
                                      capture_output=True, timeout=20,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if proc.returncode == 0:
                    names = [line[7:] for line in proc.stdout.decode("utf-8", "replace").splitlines()
                             if line.startswith("Path = ")]
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.TimeoutExpired):
        pass
    safe = []
    for name in names[:100000]:
        name = name.replace("\\", "/").strip("/")
        if not name or ":" in name or ".." in name.split("/"):
            continue
        if name.lower().startswith("data/"):
            name = name[5:]
        safe.append(name)
    return safe


def suggest_install(job: dict, mods_path: Path) -> dict:
    archive = Path(job["archivePath"])
    entries = installed_mods(mods_path)
    names = [normalized_name(job.get("modName")), normalized_name(archive.name)]
    names = [n for n in names if n]
    game = normalize_game(job.get("game"))
    members = archive_members(archive)
    # A few distinctive plugin/script paths are useful even if a mod has been renamed.
    distinctive = [m for m in members if Path(m).suffix.lower() in (".esp", ".esm", ".esl", ".dll")][:8]
    ranked = []
    for entry in entries:
        reasons = []
        score = 0.0
        same_game = not game or not entry["game"] or game == entry["game"]
        if not same_game:
            continue
        if str(job.get("modId") or "0") != "0" and str(job.get("modId")) == entry["modId"]:
            score += 100
            reasons.append("Nexus 模组编号相同" + ("（旧模组未记录游戏）" if not entry["game"] else ""))
        if job.get("fileId") and str(job["fileId"]) in entry["fileIds"]:
            score += 60
            reasons.append("Nexus 文件编号相同")
        if entry["archive"] and entry["archive"].casefold() == archive.name.casefold():
            score += 120
            reasons.append("原安装包文件名相同")
        targets = [normalized_name(entry["name"]), normalized_name(entry["archive"])]
        similarity = max((difflib.SequenceMatcher(None, n, t).ratio()
                          for n in names for t in targets if t), default=0)
        if similarity >= .5:
            score += similarity * 45
            reasons.append("名称相似度 %d%%" % round(similarity * 100))
        overlap = [m for m in distinctive if (entry["path"] / m).is_file()]
        if overlap:
            score += min(len(overlap) * 20, 60)
            reasons.append("含相同文件路径：" + "、".join(overlap[:2]))
        if reasons:
            ranked.append({"name": entry["name"], "reason": "；".join(reasons), "score": score})
    ranked.sort(key=lambda e: (-e["score"], e["name"].casefold()))
    candidates = ranked[:12]
    fallback = str(job.get("modName") or re.sub(r"-\d{1,9}-.*$", "", archive.stem))
    fallback = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", fallback).strip(" .")[:180] or "New mod"
    try:
        validate_name(fallback)
    except ValueError:
        fallback = "Mod " + fallback
    if candidates:
        return {"suggestedName": candidates[0]["name"], "matchReason": candidates[0]["reason"],
                "candidates": candidates}
    return {"suggestedName": fallback,
            "matchReason": "未找到可靠的已安装模组匹配，请确认或修改名称", "candidates": []}


def fingerprint(path: Path) -> dict:
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("安装包不能是链接")
    stat = path.stat()
    if not path.is_file() or path.suffix.lower() not in ARCHIVE_EXTENSIONS:
        raise ValueError("仅支持已完成下载的 ZIP、7z、RAR 或 FOMOD 安装包")
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ino": stat.st_ino}


class MO2Manager:
    def __init__(self, root: Path, assets: Path):
        self.root = root
        self.assets = assets
        self.directory = root / "cache" / "mo2"
        self.state_file = self.directory / "install-jobs.json"
        # Keep the existing jobs format intact. Compact IDs outlive visible
        # history because the download host can replay old completed jobs.
        self.tombstone_file = self.directory / "install-history-tombstones.json"
        self.settings_file = root / "settings.json"
        self.lock = threading.RLock()
        self.error = ""
        self.jobs = {}
        self.tombstones = set()
        self.offsets = {}
        self._bridge_checks = set()
        self._bridge_update_errors = {}
        try:
            self.jobs = read_json(self.state_file, {})
            if not isinstance(self.jobs, dict):
                raise ValueError("安装记录格式无效")
            for key, job in self.jobs.items():
                if (not isinstance(job, dict) or job.get("id") != key
                        or not isinstance(job.get("archivePath"), str)
                        or job.get("state") not in {"awaiting_confirmation", "queued", "installing", "installed", "cancelled", "error"}
                        or ("deleteAfterInstall" in job and type(job["deleteAfterInstall"]) is not bool)):
                    raise ValueError("安装记录字段无效")
                if job["state"] in ("queued", "installing") and not all(
                        isinstance(job.get(field), str) and job[field]
                        for field in ("attempt", "queueDir", "installName")):
                    raise ValueError("安装队列记录不完整")
            tombstones = read_json(self.tombstone_file, {"version": 1, "ids": []})
            if (not isinstance(tombstones, dict) or tombstones.get("version") != 1
                    or not isinstance(tombstones.get("ids"), list)
                    or any(not isinstance(key, str) for key in tombstones["ids"])):
                raise ValueError("安装历史去重记录格式无效")
            self.tombstones = set(tombstones["ids"])
        except (OSError, ValueError) as exc:
            # Never replace unreadable user data with an empty state.
            self.jobs = {}
            self.error = "安装记录无法读取，已暂停安装：" + str(exc)

    def settings(self) -> dict:
        data = read_json(self.settings_file, {})
        if not isinstance(data, dict) or not isinstance(data.get("MO2", {}), dict):
            raise ValueError("settings.json 安装设置格式无效，原文件已保留")
        saved = data.get("MO2", {})
        for key in ("enabled", "deleteAfterInstall"):
            if key in saved and type(saved[key]) is not bool:
                raise ValueError("settings.json 的 MO2.%s 必须为 true 或 false，原文件已保留" % key)
        if "mo2Path" in saved and (not isinstance(saved["mo2Path"], str) or not saved["mo2Path"].strip()):
            raise ValueError("settings.json 的 MO2.mo2Path 必须为目录路径")
        retention = saved.get("historyRetentionDays", 30)
        if type(retention) is not int or retention not in HISTORY_RETENTION_DAYS:
            raise ValueError("安装历史保留时间必须为 0、7、30 或 90 天")
        return {"enabled": saved.get("enabled", True),
                "mo2Path": saved.get("mo2Path", DEFAULT_MO2_PATH),
                "deleteAfterInstall": saved.get("deleteAfterInstall", False),
                "historyRetentionDays": retention}

    def save(self):
        if self.error:
            raise ValueError(self.error)
        atomic_json(self.state_file, self.jobs)

    def prune_history(self, retention: int) -> bool:
        """Drop expired display records without touching packages or mods.

        A durable tombstone must precede removal. If either write fails, an
        existing record or its tombstone still prevents repeat installation.
        Legacy terminal jobs receive a full retention period on first use.
        """
        if self.error:
            return False
        now = time.time()
        changed = False
        expired = set()
        for key, job in self.jobs.items():
            if job["state"] not in TERMINAL_STATES:
                continue
            if not valid_timestamp(job.get("completedAt")):
                job["completedAt"] = now
                changed = True
            # Failed installations and unsuccessful package cleanup still
            # require a player's decision and must remain available to retry.
            if (retention and job["state"] in ("installed", "cancelled")
                    and not job.get("cleanupError")
                    and job["completedAt"] < now - retention * 86400):
                expired.add(key)
        if expired:
            tombstones = self.tombstones | expired
            atomic_json(self.tombstone_file, {"version": 1, "ids": sorted(tombstones)})
            self.tombstones = tombstones
            for key in expired:
                del self.jobs[key]
            changed = True
        return changed

    def queue_dir(self, root: Path) -> Path:
        identity = hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:20]
        return self.directory / "bridge" / identity

    def bridge_status(self, root: Path) -> dict:
        update_error = self._bridge_update_errors.get(self._bridge_key(root))
        if update_error:
            return {"state": "update_failed", "message": update_error}
        try:
            status = read_json(self.queue_dir(root) / "bridge-status.json", {})
            if time.time() - float(status.get("updated_at", 0)) < 15:
                return {**status, "message": "MO2 已连接" if status.get("state") == "ready" else "请在 MO2 中完成安装选项"}
        except (OSError, ValueError, TypeError):
            pass
        deployed = root / "plugins" / "nhd_mo2_bridge.py"
        return {"state": "needs_restart" if deployed.is_file() else "not_configured",
                "message": "请打开或重启所选 MO2，安装会在连接后继续" if deployed.is_file()
                else "选择 MO2 目录后自动配置连接；首次使用需重启 MO2"}

    @staticmethod
    def _bridge_key(root: Path) -> str:
        return os.path.normcase(str(root.resolve()))

    def _write_bridge_plugin(self, root: Path):
        source = self.assets / "mo2_plugin" / "nhd_mo2_bridge.py"
        if not source.is_file():
            raise ValueError("缺少 MO2 安装连接组件，请重新构建应用")
        plugin_dir = root / "plugins"
        target = plugin_dir / source.name
        contents = source.read_bytes()
        if not target.exists() or target.read_bytes() != contents:
            temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temp.write_bytes(contents)
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)

    def _upgrade_configured_bridge(self, root: Path):
        """Refresh this downloader's existing bridge once per selected path.

        This runs only in the enabled background monitor, never construction.
        A different queue is another downloader's connection and is untouched.
        """
        key = self._bridge_key(root)
        if key in self._bridge_checks:
            return
        self._bridge_checks.add(key)
        try:
            if (not root.is_absolute() or not (root / "ModOrganizer.exe").is_file()
                    or not (root / "plugins" / "nhd_mo2_bridge.py").is_file()):
                return
            config = read_json(root / "plugins" / "nhd_bridge.json")
            configured = config.get("queue_dir") if isinstance(config, dict) else None
            if (not isinstance(configured, str) or not Path(configured).is_absolute()
                    or self._bridge_key(Path(configured)) != self._bridge_key(self.queue_dir(root))):
                return
            self._write_bridge_plugin(root)
            self._bridge_update_errors.pop(key, None)
        except (OSError, ValueError) as exc:
            # Installation/history polling stays available if a plugin file is
            # locked. An explicit settings save or confirmation can retry.
            self._bridge_update_errors[key] = "MO2 桥接更新失败：" + str(exc)

    def deploy(self, root: Path):
        if not root.is_absolute() or not (root / "ModOrganizer.exe").is_file():
            raise ValueError("请选择包含 ModOrganizer.exe 的 MO2 目录")
        plugin_dir = root / "plugins"
        plugin_dir.mkdir(exist_ok=True)
        self._write_bridge_plugin(root)
        queue = self.queue_dir(root)
        for part in ("requests", "responses"):
            (queue / part).mkdir(parents=True, exist_ok=True)
        atomic_json(plugin_dir / "nhd_bridge.json", {"queue_dir": str(queue.resolve())})
        key = self._bridge_key(root)
        self._bridge_checks.add(key)
        self._bridge_update_errors.pop(key, None)

    def update_settings(self, body: dict):
        with self.lock:
            current = self.settings()
            new = {**current, **{k: body[k] for k in current if k in body}}
            if type(new["enabled"]) is not bool or type(new["deleteAfterInstall"]) is not bool:
                raise ValueError("安装和清理开关必须为布尔值")
            if type(new["historyRetentionDays"]) is not int or new["historyRetentionDays"] not in HISTORY_RETENTION_DAYS:
                raise ValueError("安装历史保留时间必须为 0、7、30 或 90 天")
            new["mo2Path"] = str(new["mo2Path"]).strip().strip('"')
            root = Path(new["mo2Path"])
            if not root.is_absolute():
                raise ValueError("MO2 目录必须是绝对路径")
            if new["mo2Path"] != current["mo2Path"] and any(j["state"] in ("queued", "installing") for j in self.jobs.values()):
                raise ValueError("请先完成 MO2 中的待安装任务，再更换 MO2 目录")
            if new["enabled"]:
                self.deploy(root)
            data = read_json(self.settings_file, {})
            data["MO2"] = new
            atomic_json(self.settings_file, data)
            changed = self.prune_history(new["historyRetentionDays"])
            if new["mo2Path"] != current["mo2Path"]:
                for job in self.jobs.values():
                    if job["state"] == "awaiting_confirmation":
                        job.update(suggest_install(job, self.selected_mods_path(root)))
                changed = True
            if changed:
                self.save()
            return self.status()

    def selected_mods_path(self, root: Path) -> Path:
        status = self.bridge_status(root)
        return Path(status["mods_path"]) if status.get("mods_path") else mods_directory(root)

    def status(self):
        with self.lock:
            settings = self.settings()
            return {"ok": not bool(self.error), "message": self.error, "settings": settings,
                    "bridge": {"state": "unavailable", "message": self.error} if self.error
                    else self.bridge_status(Path(settings["mo2Path"])),
                    "jobs": list(reversed(list(self.jobs.values())))}

    def completed_history(self):
        # Legacy progress is read once for migration; new terminal history is append-only.
        found = []
        for name in ("takeover-jobs.jsonl", "takeover-history.jsonl"):
            path = self.root / "cache" / name
            if name == "takeover-jobs.jsonl" and name in self.offsets:
                continue
            if not path.is_file():
                continue
            offset = self.offsets.get(name, 0)
            if path.stat().st_size < offset:
                offset = 0
            with path.open("rb") as stream:
                stream.seek(offset)
                while True:
                    before = stream.tell()
                    line = stream.readline()
                    if not line or not line.endswith(b"\n"):
                        stream.seek(before)
                        break
                    try:
                        found.append(json.loads(line))
                    except ValueError:
                        pass
                self.offsets[name] = stream.tell()
        return found

    def tick(self, downloads: list[dict]):
        with self.lock:
            if self.error:
                return
            changed = self.collect_results()
            settings = self.settings()
            changed = self.prune_history(settings["historyRetentionDays"]) or changed
            if settings["enabled"]:
                root = Path(settings["mo2Path"])
                self._upgrade_configured_bridge(root)
                for download in downloads + self.completed_history():
                    if download.get("state") != "done" or not download.get("savePath"):
                        continue
                    identity = "|".join(str(download.get(k) or "") for k in ("id", "startedAt", "savePath"))
                    key = hashlib.sha256(identity.encode()).hexdigest()[:32]
                    if key in self.jobs or key in self.tombstones:
                        continue
                    path = Path(download["savePath"])
                    if not path.is_file() or path.suffix.lower() not in ARCHIVE_EXTENSIONS:
                        continue
                    job = {"id": key, "filename": path.name, "archivePath": str(path.resolve()),
                           "state": "awaiting_confirmation", "modId": download.get("modId"),
                           "game": download.get("game"), "fileId": download.get("fileId"),
                           "modName": download.get("modName"), "message": "请确认安装名称", "archiveDeleted": False}
                    job.update(suggest_install(job, self.selected_mods_path(root)))
                    self.jobs[key] = job
                    changed = True
            if changed:
                self.save()

    def confirm(self, key: str, name: str):
        with self.lock:
            job = self.jobs[key]
            if job["state"] != "awaiting_confirmation":
                raise ValueError("此任务已提交或结束，请刷新列表")
            settings = self.settings()
            if not settings["enabled"]:
                raise ValueError("请先启用下载后安装到 MO2")
            name = validate_name(name)
            archive = Path(job["archivePath"])
            stamp = fingerprint(archive)
            root = Path(settings["mo2Path"])
            self.deploy(root)
            attempt = key + "_" + uuid.uuid4().hex[:12]
            queue = self.queue_dir(root)
            payload = {"id": attempt, "archive_path": str(archive), "install_name": name,
                       "mod_id": job.get("modId"), "game": job.get("game"), "file_id": job.get("fileId"),
                       "expected_mods_path": str(self.selected_mods_path(root)), "archive_fingerprint": stamp}
            job.update({"state": "queued", "installName": name, "attempt": attempt,
                        "queueDir": str(queue), "fingerprint": stamp,
                        "deleteAfterInstall": settings["deleteAfterInstall"], "cleanupError": "",
                        "message": "已确认名称，等待 MO2 安装窗口"})
            self.save()  # persist before publish: crashes cannot repeat an install
            atomic_json(queue / "requests" / (attempt + ".json"), payload)
            return self.status()

    def retry(self, key: str):
        with self.lock:
            job = self.jobs[key]
            if job["state"] not in ("error", "cancelled"):
                raise ValueError("只能重试已取消或失败的安装")
            fingerprint(Path(job["archivePath"]))
            job.update({"state": "awaiting_confirmation", "message": "请重新确认名称后安装"})
            job.pop("completedAt", None)
            self.save()
            return self.status()

    def skip(self, key: str):
        with self.lock:
            job = self.jobs[key]
            if job["state"] != "awaiting_confirmation":
                raise ValueError("安装已提交，请在 MO2 安装窗口中取消")
            job.update({"state": "cancelled", "message": "已跳过安装，安装包已保留",
                        "completedAt": time.time()})
            self.save()
            return self.status()

    def collect_results(self) -> bool:
        changed = False
        for job in self.jobs.values():
            if job["state"] not in ("queued", "installing"):
                continue
            queue = Path(job["queueDir"])
            attempt = job["attempt"]
            response_path = queue / "responses" / (attempt + ".json")
            try:
                response = read_json(response_path, {})
            except (OSError, ValueError):
                continue
            if response.get("id") == attempt and response.get("state") in ("installed", "cancelled", "error"):
                completed_at = response.get("completed_at")
                job.update({"state": response["state"], "message": response.get("message", ""),
                            "installedName": response.get("mod_name", ""),
                            "completedAt": completed_at if valid_timestamp(completed_at) else time.time()})
                if response["state"] == "installed" and job.get("deleteAfterInstall") is True:
                    try:
                        path = Path(job["archivePath"])
                        if fingerprint(path) != job["fingerprint"]:
                            raise ValueError("安装包在确认后发生变化，已保留")
                        if any(other is not job and other.get("archivePath") == job["archivePath"]
                               and other["state"] in ("queued", "installing", "awaiting_confirmation")
                               for other in self.jobs.values()):
                            raise ValueError("其他任务仍使用此安装包，已保留")
                        path.unlink()
                        job["archiveDeleted"] = True
                    except (OSError, ValueError) as exc:
                        job["cleanupError"] = str(exc)
                changed = True
            elif not (queue / "requests" / (attempt + ".json")).exists():
                # Observe the source BEFORE the durable claim. A concurrent
                # atomic rename must never look like an absent request and
                # allow a second attempt while the native installer is open.
                if (queue / "requests" / (attempt + ".processing")).exists():
                    if job["state"] != "installing":
                        job.update({"state": "installing", "message": "请在 MO2 中选择安装选项"})
                        changed = True
                else:
                    job.update({"state": "error", "message": "安装队列写入中断，请重试；安装包已保留",
                                "completedAt": time.time()})
                    changed = True
        return changed
