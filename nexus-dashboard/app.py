"""FastAPI backend for the Nexus Mods download-history dashboard.

Run:
    python app.py                  # http://127.0.0.1:8000
    python app.py --demo           # serve bundled demo data (no login needed)
    python app.py --port 9000 --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import ntpath
import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright

import config
import takeover as takeover_mod
from mo2 import MO2Manager, atomic_json, read_json
from scraper import NexusScraper, ProgressTracker
from session_manager import SessionManager
from wallpaper import WallpaperManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nexus.app")

# 调试日志落盘到 cache/app.log（工作区内，便于排查）
try:
    config.ensure_dirs()
    _fh = logging.FileHandler(config.CACHE_DIR / "app.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(_fh)
except Exception as _exc:  # pragma: no cover
    log.warning("could not attach file logging: %s", _exc)


class AppState:
    def __init__(self) -> None:
        self.mods: list[dict] = []
        self.last_refresh: str | None = None
        self.refresh_version = 0   # 每次成功扫描 +1：前端据此可靠感知“有新结果”
        self.refresh_error: str | None = None
        self.progress = ProgressTracker()
        self.refresh_task: asyncio.Task | None = None
        self.refresh_waiting_for_login: asyncio.Task | None = None
        self.refresh_force = False
        self.login_status: dict = {"state": "idle", "message": ""}
        self.login_task: asyncio.Task | None = None
        self.filter_stats: dict = {}


state = AppState()
session_manager = SessionManager()


# ------------------------------------------------------ takeover (fusion mode)

class TakeoverController:
    """受控浏览器下载接管：接管窗口开关 + 任务状态（由 C# 下载宿主执行）。"""

    def __init__(self) -> None:
        self.window: takeover_mod.TakeoverWindow | None = None
        self.task: asyncio.Task | None = None
        self.status: dict = {"state": "stopped", "message": ""}

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def stop(self) -> None:
        if self.running and self.window is not None:
            self.window.request_stop()

    async def start(self) -> None:
        host = await asyncio.to_thread(takeover_mod.host_status)
        if not host.get("ok"):
            self.status = {
                "state": "error",
                "message": f"C# 下载宿主不可用（{host.get('error')}）。"
                           "请确认启动窗口（cmd）中的 Downloader.Host 已就绪。",
            }
            return
        self.window = takeover_mod.TakeoverWindow(session_manager)
        self.status = {"state": "starting", "message": "正在打开接管窗口…"}
        self.task = asyncio.create_task(_takeover_serve())


takeover = TakeoverController()


async def _takeover_serve() -> None:
    w = takeover.window
    try:
        async with async_playwright() as pw:
            if w is not None:
                await w.serve(pw)
        takeover.status = {"state": "stopped", "message": "接管窗口已关闭"}
    except Exception as exc:  # pragma: no cover - surfaced via status
        log.exception("takeover window crashed")
        takeover.status = {"state": "error", "message": str(exc)}
    finally:
        takeover.task = None
        takeover.window = None


async def _stop_takeover_wait() -> None:
    takeover.stop()
    task = takeover.task
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except asyncio.TimeoutError:
            pass


def runner() -> NexusScraper:
    return NexusScraper(session_manager, max_history=config.DEFAULT_MAX_HISTORY,
                        progress=state.progress)


# ------------------------------------------------------------- background jobs


def _start_login() -> bool:
    """Reuse an active login, including its post-login refresh coordination."""
    old = state.login_task
    if old is not None and not old.done():
        log.info("login already running - reusing it")
        return False
    state.login_task = asyncio.create_task(profile_job())
    return True


async def _start_refresh(force: bool = False) -> bool:
    """Start a refresh job. A still-running old one is NEVER cancelled:
    cancelling mid-scan kills the browser page and the job dies at ~90%
    forever. 正在扫描时直接复用（返回 False 表示复用/已在进行）。"""
    old = state.refresh_task
    if old is not None and not old.done():
        state.refresh_force = state.refresh_force or force
        log.info("refresh already running - reusing it (no cancel)")
        return False
    # Capture this dependency before scheduling: login may finish before the
    # refresh task gets its first turn. The login itself must never wait on a
    # refresh that is waiting for that same login.
    caller = asyncio.current_task()
    login = state.login_task
    if login is caller or (login is not None and login.done()):
        login = None
    state.refresh_force = force
    state.refresh_waiting_for_login = login
    state.refresh_task = asyncio.create_task(_refresh_when_ready(login, caller))
    return True


async def _refresh_when_ready(login: asyncio.Task | None,
                              caller: asyncio.Task | None) -> None:
    try:
        # A login could also have started after the refresh was scheduled.
        latest = state.login_task
        if login is None and latest is not None and latest is not caller and not latest.done():
            login = latest
        while login is not None:
            state.refresh_waiting_for_login = login
            succeeded = await asyncio.shield(login)
            latest = state.login_task
            if latest is not None and latest is not login and latest is not caller:
                login = latest
                continue
            if not succeeded:
                state.refresh_error = state.login_status.get("message") or "登录未完成，未刷新"
                return
            # Always collect with the newly saved session, bypassing scan cache.
            state.refresh_force = True
            break
        state.refresh_waiting_for_login = None
        await refresh_job(force=state.refresh_force)
    finally:
        state.refresh_waiting_for_login = None


async def _refresh_after_login() -> None:
    old = state.refresh_task
    if old is not None and not old.done():
        if state.refresh_waiting_for_login is not None:
            state.refresh_force = True
            return
        # Let an already running scan finish, then refresh with the new session.
        await asyncio.shield(old)
    await _start_refresh(force=True)


async def refresh_job(force: bool = False) -> None:
    me = asyncio.current_task()
    other = state.refresh_task
    if other is not None and other is not me and not other.done():
        log.info("another refresh job is running - this one exits")
        return
    try:
        await state.progress.stage("collecting_history")
        async with async_playwright() as pw:
            sc = runner()
            mods = await sc.run(pw, force_refresh=force)
        # A successful scan may have no outstanding updates. Collection
        # failures raise instead, so only that exception path keeps old data.
        state.mods = mods
        state.filter_stats = {
            "game": config.TARGET_GAME,
            "kept": len(mods),
            "removed_old_downloads": sc.filter_removed,
        }
        state.last_refresh = time.strftime("%Y-%m-%d %H:%M:%S")
        state.refresh_version += 1
        state.refresh_error = None
    except Exception as exc:
        log.exception("refresh failed")
        state.refresh_error = str(exc)
    finally:
        await state.progress.finish()


async def profile_job() -> bool:
    state.login_status = {
        "state": "waiting",
        "message": "已用浏览器资料打开窗口；若窗口空白，请手动打开 https://www.nexusmods.com/ 登录，完成后自动继续",
    }
    count: int | None = None
    try:
        async with async_playwright() as pw:
            count = await session_manager.login_with_real_profile(pw)
        state.login_status = {
            "state": "done",
            "message": f"已保存你的真实浏览器会话（{count or 0} 个 Cookie），开始自动抓取…",
        }
        await _refresh_after_login()
        return True
    except asyncio.CancelledError:
        if state.login_task is asyncio.current_task():
            state.login_status = {"state": "idle", "message": "已取消"}
        raise
    except Exception as exc:
        log.exception("real-profile login failed")
        state.login_status = {"state": "error", "message": str(exc)}
        return False


# ------------------------------------------------------------------ fastapi
mo2_manager = MO2Manager(config.REPO_ROOT, config.BASE_DIR)
wallpaper_manager = WallpaperManager(config.REPO_ROOT / "settings.json", mo2_manager.lock)


async def _watch_mo2_downloads():
    while True:
        try:
            if not DEMO_MODE:
                host = await asyncio.to_thread(takeover_mod.host_status)
                await asyncio.to_thread(mo2_manager.tick, host.get("jobs", []))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("MO2 install monitor: %s", exc)
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app):
    watcher = asyncio.create_task(_watch_mo2_downloads())
    try:
        yield
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Nexus Mods Download History Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.middleware("http")
async def origin_guard(request, call_next):
    """Local tool: reject cross-site POSTs without a matching Origin."""
    from urllib.parse import urlparse

    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin:
            host = (request.headers.get("host") or "").lower()
            o = urlparse(origin)
            if o.netloc.lower() != host and o.hostname not in ("127.0.0.1", "localhost", "::1"):
                return JSONResponse({"detail": "forbidden origin"},
                                    status_code=403)
    return await call_next(request)


@app.get("/")
async def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/status")
async def status():
    snap = state.progress.snapshot()
    # 僵尸任务自愈：进程崩溃/取消后偶发 running=True 残留，这里回收
    task_alive = state.refresh_task is not None and not state.refresh_task.done()
    if snap["running"] and not task_alive and not DEMO_MODE:
        log.warning("healing zombie refresh state (running=True but no task)")
        await state.progress.finish()
        snap = state.progress.snapshot()
    return {
        "auth": session_manager.has_session(),
        "auth_meta": session_manager.session_meta(),
        "login": state.login_status,
        "job": snap,
        "mods_count": len(state.mods),
        "last_refresh": state.last_refresh,
        "refresh_version": state.refresh_version,
        "refresh_error": state.refresh_error,
        "filter_stats": state.filter_stats,
        "demo": DEMO_MODE,
        "max_history": config.DEFAULT_MAX_HISTORY,
        "version": config.APP_VERSION,
    }


@app.get("/api/mods")
async def mods(
    query: str = Query("", description="filter by name"),
    game: str = Query("", description="filter by game domain"),
):
    rows = state.mods
    if query:
        q = query.lower()
        rows = [m for m in rows if q in m.get("name", "").lower()
                or q in (m.get("summary", "") or "").lower()
                or q in (m.get("author", "") or "").lower()]
    if game:
        rows = [m for m in rows if m.get("game_domain", "").lower() == game.lower()]
    return {
        "total": len(rows),
        "items": rows,
        "games": sorted({m.get("game_domain", "?") for m in state.mods}),
    }


@app.post("/api/refresh")
async def refresh(force: bool = False):
    started = await _start_refresh(force=force)
    return {"started": started, "reused": not started}


@app.get("/api/progress")
async def progress():
    return state.progress.snapshot()


@app.post("/api/auth/profile")
async def auth_profile():
    """The ONLY login method: open the user's REAL Edge/Chrome profile and
    persist the nexus session from it (no password, no cookie copying)."""
    started = _start_login()
    return {"started": started, "reused": not started,
            "note": "正在用你的浏览器资料打开窗口" if started else "登录正在进行"}


@app.post("/api/auth/clear")
async def auth_clear():
    if config.SESSION_FILE.exists():
        config.SESSION_FILE.unlink()
    state.login_status = {"state": "idle", "message": "登录态已清除"}
    return {"ok": True}


@app.get("/api/diag")
async def diag():
    """Surfaced diagnostics: last log lines + session/login state."""
    lines: list[str] = []
    try:
        if (config.CACHE_DIR / "app.log").is_file():
            raw = (config.CACHE_DIR / "app.log").read_text(
                encoding="utf-8", errors="replace")
            lines = raw.splitlines()[-120:]
    except Exception as exc:  # pragma: no cover
        lines = [f"<log read failed: {exc}>"]
    return {
        "session_exists": session_manager.has_session(),
        "session_meta": session_manager.session_meta(),
        "login": state.login_status,
        "job": state.progress.snapshot(),
        "log_tail": lines,
    }


@app.get("/api/export")
async def export():
    return JSONResponse(state.mods, headers={
        "Content-Disposition": "attachment; filename=nexus-mods.json"
    })


# ---------------------------------------------------------- takeover endpoints

@app.post("/api/takeover/window")
async def takeover_window(action: str = Query("start", pattern="^(start|stop)$")):
    """打开/关闭受控下载接管窗口（所有 nexusmods.com 下载 → C# 下载宿主）。"""
    if action == "stop":
        await _stop_takeover_wait()
        takeover.status = {"state": "stopped", "message": "已请求关闭接管窗口"}
        return {"ok": True}
    if takeover.running:
        return {"ok": False, "message": "接管窗口已在运行"}
    await takeover.start()
    return {"ok": takeover.status["state"] != "error",
            "message": takeover.status["message"]}


@app.get("/api/takeover/status")
async def takeover_status():
    host = await asyncio.to_thread(takeover_mod.host_status)
    jobs = host.get("jobs", []) if host.get("ok") else []
    return {
        "window": takeover.status,
        "window_running": takeover.running,
        "host": host,
        "jobs": jobs,
    }


@app.post("/api/takeover/open")
async def takeover_open(request: Request):
    """点仪表盘里的模组名：自动打开接管窗口并直达该模组的文件页(?tab=files)。
    body 可带 dir —— 仅本次下载使用的临时目录（可选）。"""
    try:
        body = await request.json()
        url = str(body.get("url") or "")
        once_dir = str(body.get("dir") or "").strip() or None
    except Exception:
        raise HTTPException(400, '需要 JSON: {"url": "https://www.nexusmods.com/..."}')
    if not takeover_mod.is_nexus_url(url):
        raise HTTPException(400, "只允许 nexusmods.com 的地址")
    files_url = takeover_mod.to_files_tab(url)

    if not takeover.running:
        await takeover.start()
        if takeover.status.get("state") == "error":
            return {"ok": False, "message": takeover.status.get("message", "打开接管窗口失败")}
    if takeover.window is not None:
        takeover.window.set_next_dir(once_dir)      # 本次下载目录（一次性）
        takeover.window.request_open(files_url)      # 先排队：窗口就绪后自动打开，请求超时也不丢

    waited = 0.0
    while waited < 40:
        w = takeover.window
        if w is not None and w.context is not None:
            return {"ok": True, "message": "已在接管窗口打开文件页", "url": files_url}
        if takeover.status.get("state") == "error":
            return {"ok": False, "message": takeover.status.get("message", "打开接管窗口失败")}
        await asyncio.sleep(0.5)
        waited += 0.5
    return {"ok": True, "message": "接管窗口仍在打开中，地址已排队", "url": files_url}


# ------------------------------------------------------------- 下载目录设置
SETTINGS_FILE = config.REPO_ROOT / "settings.json"
_thread_settings_lock = threading.Lock()


def _settings_object() -> dict:
    data = read_json(SETTINGS_FILE, {})
    if not isinstance(data, dict):
        raise ValueError("设置文件无效，原文件已保留")
    return data


def _saved_max_threads() -> int:
    try:
        with mo2_manager.lock:
            value = _settings_object().get("MaxThreads", 256)
        return value if type(value) is int and 1 <= value <= 4096 else 256
    except (OSError, ValueError):
        return 256


def _load_download_dir() -> str:
    try:
        if SETTINGS_FILE.is_file():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            d = data.get("DefaultDownloadDir", "")
            if isinstance(d, str) and d.strip():
                return d.strip()
    except Exception:
        pass
    return ""


@app.get("/api/download/dir")
async def download_dir_get():
    return {"dir": _load_download_dir() or str(config.REPO_ROOT / "cache")}


# -------------------------------------------------------------- 界面设置
def _appearance_settings(body: dict | None = None) -> dict:
    with mo2_manager.lock:
        data = _settings_object()
        appearance = data.get("Appearance", {})
        if not isinstance(appearance, dict):
            raise ValueError("界面设置无效，原文件已保留")
        value = appearance.get("fontSize", 13)
        if body is not None:
            if not isinstance(body, dict):
                raise ValueError("界面设置无效")
            value = body.get("fontSize", value)
        if type(value) is not int or not 11 <= value <= 22:
            raise ValueError("字号需为 11–22 的整数")
        if body is not None:
            data["Appearance"] = {**appearance, "fontSize": value}
            atomic_json(SETTINGS_FILE, data)
        return {"ok": True, "fontSize": value}


@app.get("/api/appearance/settings")
async def appearance_settings_get():
    try:
        return await asyncio.to_thread(_appearance_settings)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/appearance/settings")
async def appearance_settings_set(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("界面设置无效")
        return await asyncio.to_thread(_appearance_settings, body)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))


# -------------------------------------------------------------- 本地背景
@app.get("/api/wallpaper/settings")
async def wallpaper_settings_get():
    try:
        return await asyncio.to_thread(wallpaper_manager.settings)
    except (ValueError, OSError):
        return {"ok": False, "message": "背景设置不可用"}


@app.post("/api/wallpaper/settings")
async def wallpaper_settings_set(request: Request):
    try:
        body = await request.json()
        return await asyncio.to_thread(wallpaper_manager.update_settings, body)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/wallpaper/next")
async def wallpaper_next(exclude: str = ""):
    try:
        return await asyncio.to_thread(wallpaper_manager.next_image, exclude)
    except (ValueError, OSError):
        return {"ok": False, "message": "背景不可用"}


@app.get("/api/wallpaper/image/{token}")
async def wallpaper_image(token: str):
    try:
        path = await asyncio.to_thread(wallpaper_manager.image_path, token)
    except (ValueError, OSError):
        raise HTTPException(404, "图片不可用")
    return FileResponse(path, headers={"Cache-Control": "no-store",
                                       "X-Content-Type-Options": "nosniff"})


# -------------------------------------------------------------- MO2 安装
async def _mo2_call(method, *args):
    try:
        return await asyncio.to_thread(method, *args)
    except KeyError:
        raise HTTPException(404, "安装任务不存在")
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/mo2/status")
async def mo2_status():
    return await _mo2_call(mo2_manager.status)


@app.post("/api/mo2/settings")
async def mo2_settings(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "需要安装设置对象")
    return await _mo2_call(mo2_manager.update_settings, body)


@app.post("/api/mo2/jobs/{job_id}/confirm")
async def mo2_confirm(job_id: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("installName"), str):
        raise HTTPException(400, "请填写安装名称")
    return await _mo2_call(mo2_manager.confirm, job_id, body["installName"])


@app.post("/api/mo2/jobs/{job_id}/retry")
async def mo2_retry(job_id: str):
    return await _mo2_call(mo2_manager.retry, job_id)


@app.post("/api/mo2/jobs/{job_id}/skip")
async def mo2_skip(job_id: str):
    return await _mo2_call(mo2_manager.skip, job_id)


# ------------------------------------------------------------ 并发线程设置
@app.get("/api/takeover/threads")
async def takeover_threads_get():
    host = await asyncio.to_thread(takeover_mod.host_status)
    if not host.get("ok"):
        return {"ok": False, "applied": False,
                "maxThreads": await asyncio.to_thread(_saved_max_threads),
                "message": host.get("error", "宿主不可用")}
    return {"ok": True, "applied": True, "maxThreads": host.get("maxThreads", 256)}


def _apply_max_threads(value: int) -> dict:
    import urllib.request as _ur

    # Only Python writes settings.json. Serialize host application and persistence
    # without keeping the shared settings lock held during the HTTP request.
    with _thread_settings_lock:
        try:
            with mo2_manager.lock:
                _settings_object()
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"设置无法读取：{exc}")
        payload = json.dumps({"maxThreads": value}).encode()
        req = _ur.Request(takeover_mod.host_base() + "/api/settings", data=payload,
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with _ur.urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            if (not isinstance(result, dict) or result.get("ok") is not True
                    or type(result.get("maxThreads")) is not int
                    or result["maxThreads"] != value):
                raise ValueError("下载服务未确认线程设置")
        except Exception as exc:
            raise HTTPException(502, f"线程未确认应用，未保存：{exc}")
        try:
            with mo2_manager.lock:
                data = _settings_object()
                data["MaxThreads"] = value
                atomic_json(SETTINGS_FILE, data)
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"线程已应用，但保存失败：{exc}")
        return {"ok": True, "applied": True, "maxThreads": value}


@app.post("/api/takeover/threads")
async def takeover_threads_set(request: Request):
    """调整新任务并发线程数，并保存供宿主下次启动读取。"""
    try:
        body = await request.json()
        v = body.get("maxThreads") if isinstance(body, dict) else None
    except Exception:
        raise HTTPException(400, '需要 JSON: {"maxThreads": 256}')
    if type(v) is not int or not 1 <= v <= 4096:
        raise HTTPException(400, "线程数需为 1~4096 的整数")
    return await asyncio.to_thread(_apply_max_threads, v)


@app.post("/api/takeover/cancel")
async def takeover_cancel(request: Request):
    """实时取消下载任务（排队中/下载中均可）。"""
    try:
        body = await request.json()
        job_id = str(body.get("jobId") or "").strip()
    except Exception:
        raise HTTPException(400, '需要 JSON: {"jobId": "T001"}')
    if not job_id:
        raise HTTPException(400, "缺少 jobId")
    import urllib.request as _ur

    payload = json.dumps({"jobId": job_id}).encode()
    req = _ur.Request(takeover_mod.host_base() + "/api/cancel", data=payload,
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _ur.urlopen(req, timeout=5) as r:
            j = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(502, f"宿主不可用：{exc}")
    return {"ok": j.get("ok", False), "message": j.get("message", "")}


@app.post("/api/download/dir")
async def download_dir_set(request: Request):
    """设定默认下载目录：写入仓库根 settings.json（C# 宿主的默认目录，即改即用）。"""
    try:
        body = await request.json()
        d = str(body.get("dir") or "").strip().strip('"')
    except Exception:
        raise HTTPException(400, '需要 JSON: {"dir": "D:\\\\Downloads"}')
    if not d:
        raise HTTPException(400, "下载目录不能为空")
    drive, _ = ntpath.splitdrive(d)
    if drive and ntpath.isabs(d):
        d = ntpath.normpath(d)
    elif os.name != "nt" and os.path.isabs(d):
        d = os.path.normpath(d)
    else:
        raise HTTPException(400, "请填写绝对路径，例如 D:\\Downloads")
    try:
        config.ensure_dirs()
        with mo2_manager.lock:
            data = _settings_object()
            data["DefaultDownloadDir"] = d
            atomic_json(SETTINGS_FILE, data)
    except Exception as exc:
        raise HTTPException(500, f"写入失败：{exc}")
    return {"ok": True, "dir": d}


@app.post("/api/download/choose-dir")
async def download_choose_dir():
    """弹出 Windows 原生文件夹选择框，返回所选目录（取消返回空 dir）。"""
    import subprocess

    if sys.platform != "win32":
        return {"ok": False, "message": "仅 Windows 支持本地目录选择；也可直接输入路径"}
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$f.Description = '选择文件夹'; "
        "$f.ShowNewFolderButton = $true; "
        "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ Write-Output $f.SelectedPath }"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True, text=True, timeout=180)
        out = (r.stdout or "").strip()
    except Exception as exc:
        return {"ok": False, "message": f"打开目录选择失败：{exc}"}
    if r.returncode != 0 or not out:
        return {"ok": True, "dir": ""}   # 用户取消
    return {"ok": True, "dir": out}


@app.post("/api/download/openfolder")
async def download_open_folder(request: Request):
    """在资源管理器中打开某个已下载文件所在的文件夹（Windows）。"""
    try:
        body = await request.json()
        path = str(body.get("path") or "")
    except Exception:
        raise HTTPException(400, '需要 JSON: {"path": "D:\\\\Downloads\\\\x.7z"}')
    if not path:
        raise HTTPException(400, "缺少文件路径")
    folder = os.path.dirname(os.path.abspath(path)) or os.path.abspath(path)
    try:
        if not os.path.isdir(folder):
            raise FileNotFoundError(folder)
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        else:
            return {"ok": False, "message": folder}
    except Exception as exc:
        raise HTTPException(500, f"打开文件夹失败：{exc}")
    return {"ok": True, "message": folder}


# ----------------------------------------------------- "discard" tags (post-processing)
def _load_discarded() -> list[int]:
    """用户手动“丢弃”的模组 id 清单（独立于已抓取的缓存数据）。"""
    try:
        if config.DISCARD_FILE.is_file():
            data = json.loads(config.DISCARD_FILE.read_text(encoding="utf-8"))
            return sorted({int(x) for x in data.get("ids", [])})
    except Exception:
        pass
    return []


def _save_discarded(ids: list[int]) -> None:
    config.ensure_dirs()
    config.DISCARD_FILE.write_text(
        json.dumps({"ids": sorted(ids)}, ensure_ascii=False), encoding="utf-8"
    )


@app.get("/api/discarded")
async def discarded():
    return {"ids": _load_discarded()}


@app.post("/api/discarded/toggle")
async def discarded_toggle(request: Request):
    try:
        body = await request.json()
        mod_id = int(body.get("mod_id"))
    except Exception:
        raise HTTPException(400, "需要 JSON: {\"mod_id\": 123}")
    ids = _load_discarded()
    if mod_id in ids:
        ids.remove(mod_id)
    else:
        ids.append(mod_id)
    _save_discarded(ids)
    return {"ids": ids}


# ---------------------------------------------------------------------- main
DEMO_MODE = False


def load_demo() -> None:
    global DEMO_MODE
    DEMO_MODE = True
    if not config.DEMO_DATA_FILE.is_file():
        try:
            import make_demo
            make_demo.main()   # generate demo data next to the app (works in exe too)
        except Exception as exc:
            log.warning("demo data generation failed: %s", exc)
    if config.DEMO_DATA_FILE.is_file():
        state.mods = json.loads(config.DEMO_DATA_FILE.read_text(encoding="utf-8"))
        state.last_refresh = "demo data"
        state.refresh_version = 1
    log.info("demo mode: %d bundled mods", len(state.mods))


# ------------------------------------------------------------- exe 运行模式
def _ensure_host_process() -> subprocess.Popen | None:
    """Frozen exe: start the bundled C# downloader host next to the app."""
    if not getattr(sys, "frozen", False):
        return None
    if takeover_mod.host_status().get("ok"):
        log.info("downloader host already running - reusing it")
        return None
    exe = config.REPO_ROOT / "Downloader.Host.exe"
    if not exe.is_file():
        exe = config.BASE_DIR / "Downloader.Host.exe"
    if not exe.is_file():
        log.warning("Downloader.Host.exe not found - takeover disabled")
        return None
    print(f"Starting downloader host: {exe}")
    return subprocess.Popen([str(exe)], cwd=str(config.REPO_ROOT))


def _open_browser_when_ready(port: int) -> None:
    """Frozen exe: open the dashboard in the default browser once it is up.
    Skipped when a desktop shell hosts the UI (NEXUS_NO_AUTOBROWSER=1)."""
    if os.environ.get("NEXUS_NO_AUTOBROWSER") == "1":
        return
    import urllib.request
    import webbrowser

    def _wait() -> None:
        url = f"http://127.0.0.1:{port}"
        for _ in range(30):
            try:
                urllib.request.urlopen(f"{url}/api/status", timeout=1).close()
                webbrowser.open(url)
                return
            except Exception:
                time.sleep(1)

    threading.Thread(target=_wait, daemon=True).start()


def _housekeep() -> None:
    """Startup housekeeping: keep regenerable caches bounded and delete
    anything that is safe to regenerate automatically.

    Never touched: download products (user data), session state, discard
    marks, and the small per-mod scrape caches used for filename resolution.
    """
    # 1) bounded logs
    for name in ("app.log", "crash.log"):
        try:
            log_file = config.CACHE_DIR / name
            if log_file.is_file() and log_file.stat().st_size > 2 * 1024 * 1024:
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                log_file.write_text("\n".join(lines[-2000:]) + "\n", encoding="utf-8")
        except Exception:
            pass
    # 2) stale engine temp fragments (24h+: orphans; active downloads keep writing)
    try:
        cutoff = time.time() - 24 * 3600
        for p in config.REPO_ROOT.glob("cache/*.tmp"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    log.info("cleaned stale temp fragment: %s", p.name)
            except Exception:
                pass
    except Exception:
        pass
    # 3) pip download cache (regenerable)
    try:
        if config.PIP_CACHE_DIR.is_dir():
            for p in config.PIP_CACHE_DIR.rglob("*"):
                try:
                    if p.is_file():
                        p.unlink()
                except Exception:
                    pass
            for d in sorted(config.PIP_CACHE_DIR.rglob("*"), reverse=True):
                try:
                    if d.is_dir():
                        d.rmdir()
                except Exception:
                    pass
    except Exception:
        pass


if __name__ == "__main__":
    import faulthandler
    import traceback
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--demo", action="store_true",
                        help="serve bundled demo data (no login / no network)")
    args = parser.parse_args()
    config.ensure_dirs()
    # 崩溃取证：faulthandler 捕获段错误/信号，uvicorn 退出路径写 crash.log
    try:
        crash_f = open(config.CACHE_DIR / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(crash_f)
    except Exception:
        pass
    try:
        with open(config.CACHE_DIR / "server.pid", "w", encoding="utf-8") as pf:
            pf.write(str(os.getpid()))
        with open(config.CACHE_DIR / "crash.log", "a", encoding="utf-8") as f:
            f.write(f"\n=== v{config.APP_VERSION} start pid={os.getpid()} "
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    except Exception:
        pass
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning(
            "监听在 %s:%s —— 页面与 /api/export 无鉴权，仅供可信网络使用",
            args.host, args.port,
        )
    if args.demo:
        load_demo()
    print(f"Nexus Mods Dashboard starting (v{config.APP_VERSION}, pid={os.getpid()})")
    log.info("Nexus Mods Dashboard v%s starting (pid=%d)",
             config.APP_VERSION, os.getpid())
    _housekeep()
    host_proc = None
    if getattr(sys, "frozen", False):
        host_proc = _ensure_host_process()
        _open_browser_when_ready(args.port)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except BaseException as exc:
        with open(config.CACHE_DIR / "crash.log", "a", encoding="utf-8") as f:
            f.write(f"\n=== uvicorn exit {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            traceback.print_exc(file=f)
        print(f"[SERVER EXITED] {exc}  -> 详见 cache/crash.log")
        raise
    finally:
        if host_proc is not None and host_proc.poll() is None:
            log.info("stopping downloader host (pid=%d)", host_proc.pid)
            try:
                host_proc.terminate()
            except Exception:
                pass
