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
import os
import subprocess
import sys
import threading
import time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright

import config
import takeover as takeover_mod
from scraper import NexusScraper, ProgressTracker
from session_manager import SessionManager

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


def _replace_login_task(coro) -> None:
    """Start a new auth flow; a still-running old one is cancelled first
    (no more HTTP 409 '登录流程已在进行' dead-ends)."""
    old = state.login_task
    if old is not None and not old.done():
        log.info("cancelling previous auth flow to replace it")
        old.cancel()
    state.login_task = asyncio.create_task(coro)


async def _start_refresh(force: bool = False) -> bool:
    """Start a refresh job. A still-running old one is NEVER cancelled:
    cancelling mid-scan kills the browser page and the job dies at ~90%
    forever. 正在扫描时直接复用（返回 False 表示复用/已在进行）。"""
    old = state.refresh_task
    if old is not None and not old.done():
        log.info("refresh already running - reusing it (no cancel)")
        return False
    state.refresh_task = asyncio.create_task(refresh_job(force=force))
    return True


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
        if mods:
            state.mods = mods
            state.filter_stats = {
                "game": config.TARGET_GAME,
                "kept": len(mods),
                "removed_old_downloads": sc.filter_removed,
            }
            state.last_refresh = time.strftime("%Y-%m-%d %H:%M:%S")
            state.refresh_version += 1
            state.refresh_error = None
        else:
            # 空扫描（页面视图未就绪/会话异常等）绝不覆盖已有列表：
            # 保留上次数据，仅记日志，避免“好数据变暂无数据”
            log.warning("scan returned 0 mods - keeping previous list (%d items)",
                        len(state.mods))
            state.refresh_error = None
    except Exception as exc:
        log.exception("refresh failed")
        state.refresh_error = str(exc)
    finally:
        await state.progress.finish()


async def profile_job() -> None:
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
        # 登录成功后自动开始抓取（单任务锁，避免并发抓取互相打崩）
        if not state.progress.snapshot()["running"]:
            await _start_refresh()
    except asyncio.CancelledError:
        if state.login_task is asyncio.current_task():
            state.login_status = {"state": "idle", "message": "已取消"}
        raise
    except Exception as exc:
        log.exception("real-profile login failed")
        state.login_status = {"state": "error", "message": str(exc)}


# ------------------------------------------------------------------ fastapi
app = FastAPI(title="Nexus Mods Download History Dashboard")
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
    _replace_login_task(profile_job())
    return {"started": True, "note": "正在用你的浏览器资料打开窗口"}


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


# ------------------------------------------------------------ 并发线程设置
@app.get("/api/takeover/threads")
async def takeover_threads_get():
    host = await asyncio.to_thread(takeover_mod.host_status)
    if not host.get("ok"):
        return {"ok": False, "maxThreads": None, "message": host.get("error", "宿主不可用")}
    return {"ok": True, "maxThreads": host.get("maxThreads", 64)}


@app.post("/api/takeover/threads")
async def takeover_threads_set(request: Request):
    """实时调整宿主并发线程数（对新下载任务生效）。"""
    try:
        body = await request.json()
        v = int(body.get("maxThreads") or 0)
    except Exception:
        raise HTTPException(400, '需要 JSON: {"maxThreads": 64}')
    if not 1 <= v <= 4096:
        raise HTTPException(400, "线程数需在 1~4096")
    import urllib.request as _ur

    payload = json.dumps({"maxThreads": v}).encode()
    req = _ur.Request(takeover_mod.host_base() + "/api/settings", data=payload,
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _ur.urlopen(req, timeout=5) as r:
            j = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(502, f"宿主不可用：{exc}")
    return {"ok": j.get("ok", False), "maxThreads": j.get("maxThreads", v)}


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
    d = d.rstrip("/\\")
    if not ((len(d) >= 2 and d[1] == ":") or d.startswith("/")):
        raise HTTPException(400, "请填写绝对路径，例如 D:\\Downloads")
    try:
        config.ensure_dirs()
        data = {}
        if SETTINGS_FILE.is_file():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["DefaultDownloadDir"] = d
        SETTINGS_FILE.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
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
        "$f.Description = '选择下载目录'; "
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