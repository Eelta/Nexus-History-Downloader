"""下载接管：在受控浏览器窗口内捕获 nexusmods.com 下载并转交 C# 下载宿主。

不依赖任何浏览器扩展：
- 用 dashboard 的会话登录态（cache/session.json）打开一个带真实 Cookie 的
  Edge/Chrome 窗口（优先系统浏览器，与仪表盘登录一致）；
- 该窗口内任何 nexusmods.com 系文件下载（含 Slow download、保存链接）都会被
  Playwright 的 download 事件捕获；
- 先把 URL + 会话 Cookie 提交给 C# 下载宿主（HTTP，端口 18765），宿主确认
  入队后才取消浏览器自己的下载——宿主不可用时浏览器原下载不受影响；
- 下载由 256 线程分段引擎执行，进度显示在启动窗口（cmd）与仪表盘。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import config

log = logging.getLogger("nexus.takeover")

HOST_PORT_ENV = "CUSTOMDL_HOST_PORT"


def host_base() -> str:
    port = os.environ.get(HOST_PORT_ENV, "18765")
    return f"http://127.0.0.1:{port}"


def is_nexus_url(url: str) -> bool:
    """Nexus 系下载来源：
    - *.nexusmods.com（www / download / staticdelivery 等）
    - *.nexus-cdn.com（会员 CDN：supporter-files.nexus-cdn.com 等）
    """
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    if host == "nexusmods.com" or host.endswith(".nexusmods.com"):
        return True
    if host.endswith(".nexus-cdn.com"):
        return True
    return False


def to_files_tab(url: str) -> str:
    """把模组页地址规整到「文件」标签（?tab=files）。"""
    try:
        u = urlparse(url)
        q = dict(parse_qsl(u.query))
        q["tab"] = "files"
        return urlunparse(u._replace(query=urlencode(q)))
    except Exception:
        return url


def host_status() -> dict:
    """C# 下载宿主的健康状态（含最近任务）。"""
    try:
        with urllib.request.urlopen(host_base() + "/api/status", timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def submit_download(url: str, filename: str | None, cookie: str | None,
                    referrer: str | None, save_dir: str | None = None) -> dict:
    """把下载交给 C# 宿主；返回其回执 {ok, jobId, message}。"""
    payload = json.dumps({
        "type": "download",
        "url": url,
        "filename": filename,
        "cookie": cookie,
        "referrer": referrer,
        "saveDir": save_dir,
    }).encode("utf-8")
    req = urllib.request.Request(
        host_base() + "/api/takeover", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _cookie_header(cookies: list[dict]) -> str:
    return "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies)


def _clean_filename(name: str | None) -> str | None:
    """只信任看起来像真实文件名的建议名：有扩展名、无路径/查询字符、长度合理。

    弹窗页/重定向页给出的建议名（如 Download、DownloadPopUp 等）会被拒绝，
    交给宿主按 URL 兜底取名。
    """
    if not name:
        return None
    name = name.strip()
    if not name or len(name) > 200:
        return None
    if any(ch in name for ch in "/\\?:*\"<>|"):
        return None
    if "." not in name:
        return None
    return name


_MOD_PATH_RE = re.compile(r"/([a-z0-9_-]+)/mods/(\d+)(?:/files/(\d+))?", re.I)


def _url_mod_info(url: str) -> tuple[str, int, str] | None:
    """从下载地址解析 (game_domain, mod_id, file_id)。"""
    try:
        m = _MOD_PATH_RE.search(url)
        if not m:
            return None
        domain = m.group(1)
        mod_id = int(m.group(2))
        fid = m.group(3)
        if not fid:
            for k, v in parse_qsl(urlparse(url).query):
                if k == "fid":
                    fid = v
                    break
        if not fid:
            return None
        return domain, mod_id, fid
    except Exception:
        return None


def _scraped_filename(info) -> str | None:
    """用仪表盘已抓取的模组文件表（file_id → 真实文件名）解析下载的真名。

    Nexus 新版下载地址可能是签名 URL（路径尾段为 UUID 令牌），此时 URL 与
    浏览器建议名都不可信；文件表里的 `name` 才是真实文件名。
    """
    if not info:
        return None
    domain, mod_id, fid = info
    try:
        p = config.MODS_CACHE_DIR / f"{domain}-{mod_id}.json"
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        # 无时间缓存：只要代码版本一致就复用；未命中 fid 时由在线解析兜底
        if data.get("app_version") != config.APP_VERSION:
            return None
        files = data.get("files") or {}
        for items in files.values():
            for f in items:
                if str(f.get("file_id")) == str(fid):
                    return _clean_filename(str(f.get("name") or "").strip())
    except Exception:
        return None
    return None


def _combined_info(download_url: str, page_url: str | None):
    """解析 (domain, mod_id, file_id)：先看下载地址，格式变化时用当前模组页补全。"""
    info = _url_mod_info(download_url)
    if info:
        return info
    domain: str | None = None
    mod_id: int | None = None
    if page_url:
        m = re.search(r"/([a-z0-9_-]+)/mods/(\d+)", page_url, re.I)
        if m:
            domain, mod_id = m.group(1), int(m.group(2))
    fid: str | None = None
    for k, v in parse_qsl(urlparse(download_url).query):
        if k == "fid":
            fid = v
            break
    if domain and mod_id is not None and fid:
        return domain, mod_id, fid
    return None


class TakeoverWindow:
    """常驻的受控浏览器窗口：所有 nexusmods.com 下载默认被接管。

    会话失效时自动用真实浏览器资料窗口补登一次（复用仪表盘登录机制）。
    用户手动关掉窗口的所有标签页后任务自动结束。
    """

    def __init__(self, session_manager):
        self.sm = session_manager
        self._stop = asyncio.Event()
        self._pending_urls: list[str] = []
        self._next_dir: str | None = None
        self._last_page_url: str | None = None
        self.context = None
        self.browser = None

    def request_stop(self) -> None:
        self._stop.set()

    def request_open(self, url: str) -> None:
        """把地址排队：窗口就绪后自动打开（即使窗口尚未打开也不丢）。"""
        self._pending_urls.append(url)

    def set_next_dir(self, dir_path: str | None) -> None:
        """本次下载的临时目录（一次性：下一个捕获的下载使用后即清除）。"""
        self._next_dir = (dir_path or "").strip() or None

    async def serve(self, playwright) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            browser = context = None
            try:
                browser, context = await self._open_takeover_context(playwright)
                self.browser, self.context = browser, context
                log.info(
                    "接管窗口已打开：该窗口内所有 nexusmods.com 下载将转交下载宿主"
                )
                # 保持窗口存活；把排队的地址在窗口里打开（首个复用空白页，其余开新标签）
                while not self._stop.is_set() and context.pages:
                    while self._pending_urls:
                        url = self._pending_urls.pop(0)
                        try:
                            target = None
                            for p in context.pages:
                                try:
                                    if (p.url or "") in ("about:blank", ""):
                                        target = p
                                        break
                                except Exception:
                                    pass
                            if target is None:
                                target = await context.new_page()
                            await target.goto(url, wait_until="domcontentloaded",
                                              timeout=45_000)
                            await target.bring_to_front()
                            self._last_page_url = url
                            log.info("接管窗口已打开：%s", url[:140])
                        except Exception as exc:
                            log.warning("接管窗口打开 %s 失败：%s", url[:100], exc)
                    await asyncio.sleep(1)
                # 窗口被用户关闭（所有标签页关掉）或收到停止请求：正常结束，不再自动重开
                if not self._stop.is_set():
                    log.info("接管窗口已被用户关闭，接管停止；再次点击模组名会重新打开")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if "请先在仪表盘点击" in str(exc):
                    log.warning("接管已停止：%s", exc)
                    return
                log.exception("接管窗口异常（%s），3 秒后重开", exc)
                if not self._stop.is_set():
                    await asyncio.sleep(3)
            finally:
                try:
                    if context is not None:
                        for p in list(context.pages):
                            try:
                                await p.close()
                            except Exception:
                                pass
                        await context.close()
                except Exception:
                    pass
                try:
                    if browser is not None:
                        await browser.close()
                except Exception:
                    pass
                self.browser = self.context = None
                self._pending_urls.clear()

    async def _open_takeover_context(self, playwright):
        """打开一个带会话 Cookie 的可见浏览器窗口（优先 Edge/Chrome）。"""
        browser = await self.sm._launch(playwright, headless=False)
        try:
            context = await self.sm._new_context(
                browser, storage_state=self.sm.session_file)
            await self.sm._harden(context)
            if not await self.sm._verify(context):
                log.warning("N 网会话已失效：不再自动补登，请先在仪表盘重新登录")
                try:
                    await context.close()
                    await browser.close()
                except Exception:
                    pass
                raise RuntimeError("N 网会话已失效，请先在仪表盘点击「浏览器登录」重新登录。")
            context.on("download", self._on_download)
            if not context.pages:
                await context.new_page()   # 空白页占位：serve 循环把目标地址开在这里
            return browser, context
        except Exception:
            try:
                await browser.close()
            except Exception:
                pass
            raise

    def _on_download(self, download) -> None:
        """Playwright 下载事件（含 Slow download / 保存链接等），转交宿主。"""
        asyncio.create_task(self._handle_download(download))

    async def _handle_download(self, download) -> None:
        try:
            url = getattr(download, "url", None)
            if not url:
                log.warning("捕获到无 URL 的下载，放行浏览器处理")
                return
            if self.context is None:
                log.warning("接管窗口上下文不存在，放行下载：%s", url[:120])
                return

            # 接管窗口只用于 N 网：窗口内捕获到的任何下载都视为 Nexus 下载，
            # 不再按域名白名单过滤（CDN 域名会变，白名单永远追不上）。
            log.info("捕获窗口内下载：%s", url[:160])

            suggested = _clean_filename(getattr(download, "suggested_filename", None))
            url_info = _combined_info(url, self._last_page_url)
            scraped = _scraped_filename(url_info)
            if scraped:
                filename = scraped   # 文件表真名优先（签名 URL 的 UUID 名不可信）
                log.info("文件名来自模组文件表：%s", filename)
            else:
                filename = suggested
            if not filename:
                # 文件表缓存未就绪时：在线解析（用接管窗口登录态现抓文件页）
                filename = await self._resolve_filename_from_site(self.context, url_info)
            save_dir = self._next_dir
            if save_dir:
                self._next_dir = None   # 一次性：本目录只用于下一个捕获的下载
            referrer = self._last_page_url
            page = getattr(download, "page", None)
            if page is not None:
                try:
                    referrer = page.url or referrer
                except Exception:
                    pass

            cookies: list[dict] = []
            try:
                cookies = await self.context.cookies(url)
            except Exception as exc:
                log.warning("读取 Cookie 失败（%s），将不带 Cookie 提交", exc)
            cookie = _cookie_header(cookies) or None

            log.info("下载解析：url=%s info=%s suggested=%r scraped=%r final=%r saveDir=%r",
                 url[:120], url_info, suggested, scraped, filename, save_dir)
            log.info("捕获 Nexus 下载：%s", url[:160])
            result = await asyncio.to_thread(
                submit_download, url, filename, cookie, referrer, save_dir)
            if result.get("ok"):
                # 宿主已确认入队：立即取消浏览器侧下载，避免浏览器把文件下完
                # 而产生重复文件与 Edge 的「已下载」通知。
                try:
                    await download.cancel()
                except Exception as exc:
                    log.warning("取消浏览器下载失败：%s", exc)
                log.info("已转交下载宿主 jobId=%s，浏览器下载已取消", result.get("jobId"))
            else:
                log.warning("宿主拒绝（%s）：保留浏览器原下载", result.get("message"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("接管下载失败（保留浏览器原下载）：%s", exc)

    async def _resolve_filename_from_site(self, context, info) -> str | None:
        """在线兜底：用接管窗口的登录态现抓该模组的文件页，按 file_id 找真实文件名。

        在文件表缓存未就绪（首次使用/缓存被清）时保证文件名准确。
        """
        if not info:
            return None
        domain, mod_id, fid = info
        page = None
        html = None
        try:
            from scraper import parse_files_html
            from bs4 import BeautifulSoup
        except Exception as exc:
            log.warning("在线解析文件名：缺少依赖（%s）", exc)
            return None
        page_url = f"{config.SITE}/{domain}/mods/{mod_id}?tab=files"
        try:
            resp = await context.request.get(page_url, timeout=30_000)
            if resp.status == 200:
                html = await resp.text()
            else:
                log.warning("在线解析文件名：HTTP %s（改用页面方式重试）", resp.status)
        except Exception as exc:
            log.warning("在线解析文件名：请求失败（%s，改用页面方式重试）", exc)
        if not html:
            # 纯 HTTP 请求被 Cloudflare 等拦截时，改用真实页面抓取（可执行 JS/过挑战）
            try:
                page = await context.new_page()
                await page.goto(page_url, wait_until="domcontentloaded",
                                timeout=45_000)
                await page.wait_for_timeout(1500)
                html = await page.content()
            except Exception as exc:
                log.warning("在线解析文件名（页面方式）失败：%s", exc)
        try:
            if html:
                files = parse_files_html(BeautifulSoup(html, "html.parser"), html)
                for items in files.values():
                    for f in items:
                        if str(f.get("file_id")) == str(fid):
                            name = _clean_filename(str(f.get("name") or "").strip())
                            if name:
                                log.info("文件名在线解析成功：%s", name)
                                # 命中即写缓存（无时间过期，版本校验复用），下次直接复用
                                try:
                                    config.ensure_dirs()
                                    p = config.MODS_CACHE_DIR / f"{domain}-{mod_id}.json"
                                    p.write_text(json.dumps({
                                        "app_version": config.APP_VERSION,
                                        "mod_id": mod_id,
                                        "game_domain": domain,
                                        "files": files,
                                    }, ensure_ascii=False, default=str),
                                        encoding="utf-8")
                                except Exception:
                                    pass
                                return name
        except Exception as exc:
            log.warning("在线解析文件名异常：%s", exc)
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
        return None