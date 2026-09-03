"""Central configuration for the Nexus Mods dashboard.

Path discipline: ALL downloaded / intermediate artifacts (browser binaries,
pip cache, session state, scraped mod data, demo data) live under a single
cache folder (``<repo>/cache/nexus-dashboard`` in source mode, next to the
exe in packaged mode).
"""

from __future__ import annotations

import os
import pathlib
import sys

# Paths: bundled static assets live in the PyInstaller extraction dir when
# frozen; all runtime data lives next to the exe (the install folder is the
# portable data folder: program + cache + downloads together).
if getattr(sys, "frozen", False):
    BASE_DIR = pathlib.Path(sys._MEIPASS)                       # bundled resources
    # 数据目录 = 应用所在文件夹（壳通过 NHD_APP_DIR 传入；直接运行后端时用自身目录）
    REPO_ROOT = pathlib.Path(os.environ.get(
        "NHD_APP_DIR") or pathlib.Path(sys.executable).resolve().parent)
else:
    BASE_DIR = pathlib.Path(__file__).resolve().parent          # <repo>/nexus-dashboard
    REPO_ROOT = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"

# ---- everything writeable goes next to the exe / under repo/cache --------
CACHE_DIR = REPO_ROOT / "cache" / "nexus-dashboard"

SESSION_FILE = CACHE_DIR / "session.json"          # one-time login state
SESSION_META_FILE = CACHE_DIR / "session.meta.json"  # user-agent of imported session
DEMO_DATA_FILE = CACHE_DIR / "demo_mods.json"      # generated demo data
MODS_CACHE_DIR = CACHE_DIR / "mods"                # per-mod scraped payloads
SCAN_CACHE_FILE = CACHE_DIR / "history-scan.json"  # light history scan result
# （已移除 SCAN_TTL_HOURS：无时间缓存——scan 是否重扫由 force 决定）
DISCARD_FILE = CACHE_DIR / "discarded.json"        # user "discard" tags (untouched cache)
PIP_CACHE_DIR = CACHE_DIR / "pip"                  # pip download cache

os.environ.setdefault("PIP_CACHE_DIR", str(PIP_CACHE_DIR))

SITE = "https://www.nexusmods.com"
HISTORY_URL = "https://www.nexusmods.com/users/myaccount?tab=download+history"
LOGIN_URL = "https://users.nexusmods.com/login"

# bump on each code change so the dashboard footer shows the running version
APP_VERSION = "2.34"

# --- collection behaviour ---------------------------------------------------
DEFAULT_MAX_HISTORY = None    # None = 抓取全部下载历史（不限制条数），再由
                              # 「更新日期 ≥ 下载日期」筛选出已下载但有更新的模组
TARGET_GAME = "skyrimspecialedition"  # only read Skyrim Special Edition mods
MAX_LOGIN_WAIT_MINUTES = 10    # how long the interactive login flow waits
MAX_LOAD_MORE_CLICKS = 250     # safety cap for history-page pagination rounds

# --- files tab section names (in display order) -----------------------------
FILE_SECTIONS = ["Main files", "Optional files", "Old files", "Miscellaneous"]


def ensure_dirs() -> None:
    for d in (CACHE_DIR, MODS_CACHE_DIR, PIP_CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)