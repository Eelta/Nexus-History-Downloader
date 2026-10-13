"""Read-only local wallpaper inventory and settings.

Only images discovered below the configured folder receive opaque URLs. Every
image request rechecks that path, including any changed directory links.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import secrets
import stat
import threading
import time

from mo2 import atomic_json, read_json


DEFAULT_DIRECTORY = r"E:\BaiduSyncdisk\壁纸图片"
INTERVAL_SECONDS = 300
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".gif"}
DEFAULT_SETTINGS = {"enabled": True, "directory": DEFAULT_DIRECTORY,
                    "opacity": 0.22, "intervalSeconds": INTERVAL_SECONDS}


def _is_link(path: Path) -> bool:
    details = path.lstat()
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


class WallpaperManager:
    def __init__(self, settings_file: Path, settings_lock=None) -> None:
        self.settings_file = settings_file
        # Share the MO2 lock so writing one section cannot lose another section.
        self.settings_lock = settings_lock or threading.RLock()
        self.inventory_lock = threading.RLock()
        self._root: Path | None = None
        self._directory: str | None = None
        self._scanned_at = 0.0
        self._images: dict[str, Path] = {}

    def _read_settings(self) -> tuple[dict, dict]:
        data = read_json(self.settings_file, {})
        if not isinstance(data, dict) or not isinstance(data.get("Wallpaper", {}), dict):
            raise ValueError("背景设置无效")
        values = {**DEFAULT_SETTINGS, **data.get("Wallpaper", {})}
        if type(values["enabled"]) is not bool:
            raise ValueError("背景开关无效")
        if not isinstance(values["directory"], str) or not values["directory"].strip():
            raise ValueError("请填写图片目录")
        opacity = values["opacity"]
        if type(opacity) not in (int, float) or not math.isfinite(opacity) or not 0 <= opacity <= 1:
            raise ValueError("透明度需在 0–100%")
        # Rotation cadence is fixed; stale/manual settings cannot disable it.
        values["intervalSeconds"] = INTERVAL_SECONDS
        return data, {key: values[key] for key in DEFAULT_SETTINGS}

    def settings(self) -> dict:
        with self.settings_lock:
            _, values = self._read_settings()
            return {"ok": True, **values}

    def update_settings(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("背景设置无效")
        with self.settings_lock:
            data, values = self._read_settings()
            if "enabled" in body:
                if type(body["enabled"]) is not bool:
                    raise ValueError("背景开关无效")
                values["enabled"] = body["enabled"]
            if "opacity" in body:
                value = body["opacity"]
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("透明度需在 0–100%")
                values["opacity"] = value
            if "directory" in body:
                if not isinstance(body["directory"], str):
                    raise ValueError("请填写图片目录")
                values["directory"] = body["directory"].strip()
            directory = Path(values["directory"])
            if not values["directory"] or not directory.is_absolute():
                raise ValueError("请填写完整图片目录")
            if values["enabled"] and not directory.is_dir():
                raise ValueError("图片目录不存在")
            data["Wallpaper"] = {**data.get("Wallpaper", {}), **values}
            atomic_json(self.settings_file, data)
            with self.inventory_lock:
                if self._directory != values["directory"]:
                    self._clear_inventory()
            return {"ok": True, **values}

    def _clear_inventory(self) -> None:
        self._root = None
        self._directory = None
        self._scanned_at = 0.0
        self._images = {}

    @staticmethod
    def _safe_file(path: Path, root: Path) -> Path | None:
        try:
            relative = path.relative_to(root)
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                return None
            current = root
            for part in relative.parts:
                current = current / part
                if _is_link(current):
                    return None
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                return None
            return resolved
        except (OSError, ValueError, RuntimeError):
            return None

    @staticmethod
    def _configured_root(directory: str) -> Path | None:
        try:
            path = Path(directory)
            if not path.is_absolute() or not path.is_dir():
                return None
            return path.resolve(strict=True)
        except (OSError, ValueError, RuntimeError):
            return None

    def _refresh_inventory(self, directory: str, root: Path) -> None:
        changed = self._directory != directory or self._root != root
        if not changed and time.monotonic() - self._scanned_at < INTERVAL_SECONDS:
            return
        old_tokens = {} if changed else {path: token for token, path in self._images.items()}
        images = {}
        # os.walk skips inaccessible folders; prune links and junctions explicitly
        # because Windows junctions are not necessarily reported as symlinks.
        for folder, subfolders, filenames in os.walk(root, followlinks=False):
            parent = Path(folder)
            safe_subfolders = []
            for name in subfolders:
                child = parent / name
                try:
                    if not _is_link(child) and child.resolve(strict=True).is_relative_to(root):
                        safe_subfolders.append(name)
                except (OSError, ValueError, RuntimeError):
                    pass
            subfolders[:] = safe_subfolders
            for filename in filenames:
                path = self._safe_file(parent / filename, root)
                if path is not None:
                    token = old_tokens.get(path) or secrets.token_urlsafe(24)
                    images[token] = path
        self._directory = directory
        self._root = root
        self._images = images
        self._scanned_at = time.monotonic()

    def next_image(self, exclude: str = "") -> dict:
        values = self.settings()
        if not values["enabled"]:
            return {"ok": False, "message": "背景已关闭"}
        with self.inventory_lock:
            root = self._configured_root(values["directory"])
            if root is None:
                self._clear_inventory()
                return {"ok": False, "message": "图片目录不可用"}
            self._refresh_inventory(values["directory"], root)
            # Filter deleted or replaced files even while the inventory is cached.
            available = {token: path for token, path in self._images.items()
                         if self._safe_file(path, root) is not None}
            self._images = available
            tokens = list(available)
            if len(tokens) > 1 and exclude in available:
                tokens.remove(exclude)
            if not tokens:
                return {"ok": False, "message": "目录中没有图片"}
            token = secrets.choice(tokens)
            return {"ok": True, "url": "/api/wallpaper/image/" + token,
                    "token": token, "filename": available[token].name}

    def image_path(self, token: str) -> Path:
        values = self.settings()
        with self.inventory_lock:
            root = self._configured_root(values["directory"])
            if (not values["enabled"] or root is None or root != self._root
                    or values["directory"] != self._directory):
                self._clear_inventory()
                raise FileNotFoundError("图片不可用")
            path = self._images.get(token)
            safe = self._safe_file(path, root) if path is not None else None
            if safe is None:
                raise FileNotFoundError("图片不可用")
            return safe
