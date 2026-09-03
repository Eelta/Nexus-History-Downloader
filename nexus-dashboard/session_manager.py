"""Session management: one-time interactive login, then reuse of the saved
browser storage state (cookies + localStorage).  No password is ever stored."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time

import config

log = logging.getLogger("nexus.session")


def _browser_channels() -> list[str]:
    """System browsers only: Edge or Chrome are both prerequisites.

    The bundled chromium is intentionally never used or downloaded.
    Override with env var NEXUS_BROWSER=msedge|chrome.
    """
    forced = (os.environ.get("NEXUS_BROWSER") or "").strip().lower()
    if forced in ("msedge", "chrome"):
        return [forced]
    return ["msedge", "chrome"] if sys.platform == "win32" else ["chrome"]

IS_LOGGED_IN_JS = """
() => {
  const hrefs = Array.from(document.querySelectorAll('a[href]')).map(a => a.href || '');
  const hasAccountLink = hrefs.some(h => /users\\/myaccount/i.test(h));
  const hasLoginForm = !!document.querySelector('input[type=password]');
  return { ok: hasAccountLink, hasLoginForm };
}
"""


class SessionManager:
    """Owns the Playwright context built from a persisted storage state.

    flow:
      1. if `session.json` exists -> restore it and verify the login marker;
      2. if verification fails or the file is missing -> open a *headed*
         browser, let the human log in once, then persist the state.
    """

    def __init__(self, session_file=config.SESSION_FILE,
                 max_wait_minutes=config.MAX_LOGIN_WAIT_MINUTES,
                 login_url=config.LOGIN_URL, verify_url=config.SITE):
        self.session_file = session_file
        self.max_wait_minutes = max_wait_minutes
        self.login_url = login_url
        self.verify_url = verify_url

    # ------------------------------------------------------------------ api
    def has_session(self) -> bool:
        return self.session_file.is_file()

    def session_meta(self) -> dict:
        if not self.has_session():
            return {"ok": False, "reason": "missing"}
        try:
            raw = json.loads(self.session_file.read_text(encoding="utf-8"))
            saved = raw.get("saved_at", "")
            return {"ok": True, "saved_at": saved}
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "reason": f"unreadable: {exc}"}

    async def _launch(self, playwright, headless: bool = False):
        """Launch a browser: prefer installed Edge/Chrome (no download),
        fall back to Playwright's bundled chromium.

        Automation-fingerprint masking is applied so anti-bot checks
        (Cloudflare etc.) are less likely to flag the instance.
        """
        last_err: Exception | None = None
        for channel in _browser_channels():
            kw = {
                "headless": headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            }
            if channel:
                kw["channel"] = channel
            try:
                log.info("launching browser channel=%s", channel or "chromium")
                return await playwright.chromium.launch(**kw)
            except Exception as exc:
                last_err = exc
                log.warning("browser launch channel=%s failed: %s",
                            channel or "chromium", exc)
        raise RuntimeError(
            "无法启动浏览器：需要已安装 Microsoft Edge 或 Google Chrome。\n"
            "本工具不下载浏览器；请安装其一后重试。"
        ) from last_err

    # ----------------------------------------------------- session hardening
    async def _harden(self, context) -> None:
        """Mask the most common automation signals."""
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

    def _load_ua(self) -> str | None:
        """User agent of the imported real-browser session (if any)."""
        env_ua = (os.environ.get("NEXUS_UA") or "").strip()
        if env_ua:
            return env_ua
        try:
            if config.SESSION_META_FILE.is_file():
                raw = json.loads(config.SESSION_META_FILE.read_text(encoding="utf-8"))
                ua = (raw.get("user_agent") or "").strip()
                return ua or None
        except Exception:
            pass
        return None

    async def _new_context(self, browser, storage_state=None):
        """Context with the UA of the imported session + fingerprint masking."""
        kw: dict = {}
        ua = self._load_ua()
        if ua:
            kw["user_agent"] = ua
        if storage_state is not None:
            kw["storage_state"] = str(storage_state)
        context = await browser.new_context(**kw)
        await self._harden(context)
        return context

    async def get_or_create(self, playwright, force_login: bool = False):
        """Return a BrowserContext with a verified nexus session.

        Login is NEVER automatic: the user must click「浏览器登录」once.
        A stored session is restored and verified silently; if it is
        missing or expired, a clear message asks the user to log in again.

        Returns (browser, context).  Caller is responsible for closing both.
        """
        if force_login or not self.has_session():
            raise RuntimeError("尚未登录：请在仪表盘点击「浏览器登录」完成一次登录。")

        # 1) headless verify - no window pops up for a valid stored session
        browser = await self._launch(playwright, headless=True)
        try:
            context = await self._new_context(
                browser, storage_state=self.session_file)
            if await self._verify(context):
                log.info("session restored and verified (headless)")
                return browser, context
            log.warning("headless verification failed, retrying headed")
        except Exception as exc:
            log.warning("session restore (headless) failed: %s", exc)
        await browser.close()

        # 2) headed verify - brief visible window, still no login needed
        try:
            browser = await self._launch(playwright, headless=False)
            context = await self._new_context(
                browser, storage_state=self.session_file)
            if await self._verify(context):
                log.info("session restored and verified (headed)")
                return browser, context
            log.warning("headed verification failed - session expired")
            await context.close()
        except Exception as exc:
            log.warning("session restore (headed) failed: %s", exc)
        try:
            await browser.close()
        except Exception:
            pass

        # 3) session really gone - ask for manual login (never auto-open)
        raise RuntimeError("N 网会话已失效：请在仪表盘点击「浏览器登录」重新登录。")

    # ------------------------------------------- real-browser-profile login
    def _profile_dirs(self) -> list[tuple[str, str]]:
        """(channel, user-data-dir) candidates for the user's real browser."""
        dirs: list[tuple[str, str]] = []
        if sys.platform == "win32":
            local = os.environ.get("LOCALAPPDATA", "")
            dirs.append(("msedge", os.path.join(
                local, "Microsoft", "Edge", "User Data")))
            dirs.append(("chrome", os.path.join(
                local, "Google", "Chrome", "User Data")))
        else:
            home = os.path.expanduser("~")
            dirs.append(("chrome", os.path.join(
                home, ".config", "google-chrome")))
            dirs.append(("msedge", os.path.join(
                home, ".config", "microsoft-edge")))
        return [(ch, d) for ch, d in dirs if os.path.isdir(d)]

    @staticmethod
    def _profile_locked(data_dir: str) -> bool:
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket",
                     "lockfile"):
            if os.path.exists(os.path.join(data_dir, name)):
                return True
        return False

    def _prepare_profile_link(self, channel: str, data_dir: str) -> str | None:
        """Point Playwright at the real profile via a junction/symlink inside
        the project cache.

        Chrome refuses remote debugging when the user-data-dir is the DEFAULT
        profile directory ("DevTools remote debugging requires a non-default
        data directory"), which breaks launch_persistent_context on real
        profiles.  A reparse point (junction) to the SAME folder uses the real
        cookies/profile while satisfying the "non-default" path check.
        """
        link = config.CACHE_DIR / f"profile-link-{channel}"
        try:
            if os.path.exists(link) or os.path.islink(link) or os.path.isdir(link):
                os.rmdir(link)  # removes the junction itself, never its target
        except Exception:
            pass
        try:
            config.ensure_dirs()
            if sys.platform == "win32":
                r = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(data_dir)],
                    capture_output=True, text=True, timeout=20,
                )
                if r.returncode != 0:
                    log.warning("mklink failed: %s", r.stdout or r.stderr)
                    return None
                return str(link)
            os.symlink(data_dir, link, target_is_directory=True)
            return str(link)
        except Exception as exc:
            log.warning("profile link creation failed: %s", exc)
            return None

    async def login_with_real_profile(self, playwright,
                                      max_wait_minutes: int = 8) -> int | None:
        """One-click automation: open the user's OWN Edge/Chrome profile.

        The profile already carries the real cookies / Cloudflare clearance
        / UA, so the login window looks and behaves like their normal
        browser.  If they are already logged in, zero manual steps happen.
        After the session is confirmed, ONLY nexus-domain cookies + UA are
        persisted (privacy: no other site cookies, no passwords), then the
        profile is released and later runs reuse the saved session.

        Returns the number of persisted cookies (or None).
        """
        candidates = self._profile_dirs()
        if not candidates:
            raise RuntimeError("未找到 Edge/Chrome 的浏览器资料目录")
        last_err: Exception | None = None
        context = None
        for channel, data_dir in candidates:
            if self._profile_locked(data_dir):
                last_err = RuntimeError(
                    f"{channel} 正在运行：请先完全关闭它（含右下角托盘图标），"
                    "再点「用我的浏览器登录」"
                )
                continue
            launch_dir = self._prepare_profile_link(channel, data_dir) or data_dir
            try:
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=launch_dir,
                    channel=channel,
                    headless=False,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                log.info("opened real profile via channel=%s (dir=%s)",
                         channel, launch_dir)
                break
            except Exception as exc:
                last_err = exc
                log.warning("profile launch (%s) failed: %s", channel, exc)
        if context is None:
            raise RuntimeError(
                "无法打开浏览器资料（已尝试 Edge/Chrome）。\n"
                f"最近一次错误：{last_err}\n"
                "请确认已完全关闭 Edge/Chrome（含右下角托盘图标）后重试。"
            ) from last_err
        await self._harden(context)
        log.info("profile context opened, pages=%d", len(context.pages))
        try:
            # use the VISIBLE first tab; if the browser hijacks it (welcome /
            # newtab / session-restore), retry on a fresh tab below
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.bring_to_front()
            except Exception:
                pass

            def on_nexus(p) -> bool:
                try:
                    return "nexusmods.com" in (p.url or "")
                except Exception:
                    return False

            try:
                await page.goto(config.SITE, wait_until="commit",
                                timeout=30_000)
                await page.wait_for_load_state("domcontentloaded",
                                               timeout=30_000)
            except Exception as exc:
                log.warning("first tab goto failed: %s", exc)
            await page.wait_for_timeout(2500)

            # Edge/Chrome sometimes keep the very first tab stuck on
            # about:blank / newtab - open a fresh tab and navigate there
            if not on_nexus(page):
                log.info("first tab stuck on %s - retrying on a new tab",
                         page.url or "about:blank")
                try:
                    page2 = await context.new_page()
                    await page2.goto(config.SITE,
                                     wait_until="domcontentloaded",
                                     timeout=45_000)
                    page = page2
                    for p in list(context.pages):
                        if p is not page and str(p.url or "").startswith("about:"):
                            try:
                                await p.close()
                            except Exception:
                                pass
                except Exception as exc:
                    # Navigation failed: do NOT abort - keep the window open
                    # and let the user open nexusmods.com manually.  Login
                    # detection scans ALL tabs + cookies, so a manual login
                    # is still recognized.  Save a diagnostic screenshot.
                    shot = None
                    try:
                        shot = config.CACHE_DIR / "login-debug.png"
                        await page.screenshot(path=str(shot))
                    except Exception:
                        pass
                    log.warning(
                        "new-tab navigation failed (%s); waiting for manual "
                        "login instead%s",
                        exc, f" (screenshot saved: {shot})" if shot else "",
                    )
                await page.wait_for_timeout(2500)

            if not on_nexus(page):
                try:
                    await page.reload(wait_until="domcontentloaded",
                                      timeout=30_000)
                    await page.wait_for_timeout(2000)
                except Exception as exc:
                    log.warning("reload after stuck tab failed: %s", exc)
            log.info("real-profile window url=%s", page.url)

            async def logged_in_anywhere() -> bool:
                # DOM marker in any tab ...
                for p in context.pages:
                    try:
                        res = await p.evaluate(IS_LOGGED_IN_JS)
                        if res and res.get("ok"):
                            return True
                    except Exception:
                        continue
                # ... or nexus auth cookies present anywhere
                try:
                    for d in ("https://www.nexusmods.com",
                              "https://users.nexusmods.com"):
                        names = " ".join(
                            c["name"].lower() for c in await context.cookies(d))
                        if "sid_" in names or "jwt" in names:
                            return True
                except Exception:
                    pass
                return False

            if not await logged_in_anywhere():
                log.info("not logged in yet - entering login wait")
                # surface a hard IP-level block immediately
                try:
                    text = await page.evaluate(
                        "document.body ? document.body.innerText : ''"
                    )
                    if text and "blocked" in text.lower() \
                            and "sorry" in text.lower():
                        raise RuntimeError(
                            "nexusmods.com 返回了拦截页（Sorry, you have been "
                            "blocked）。若你的日常浏览器也一样被拦，这是 IP 层面"
                            "问题：请换网络 / 等一段时间再试。"
                        )
                except RuntimeError:
                    raise
                except Exception:
                    pass
                deadline = time.monotonic() + max_wait_minutes * 60
                iters = 0
                while time.monotonic() < deadline:
                    iters += 1
                    if await logged_in_anywhere():
                        log.info("login detected (DOM marker or nexus auth cookie)")
                        break
                    if iters % 15 == 0:
                        urls = []
                        for p in context.pages:
                            try:
                                urls.append(str(p.url or "")[:120])
                            except Exception:
                                urls.append("<closed>")
                        log.info("waiting for login - tabs: %s", urls)
                    await asyncio.sleep(2)
                else:
                    shot = None
                    try:
                        shot = config.CACHE_DIR / "login-debug.png"
                        await page.screenshot(path=str(shot))
                    except Exception:
                        pass
                    log.warning("login wait timed out; screenshot=%s", shot)
                    raise TimeoutError(
                        "在浏览器资料窗口中等待登录超时（8 分钟）。"
                        "若窗口一直空白且手动打开 nexusmods.com 也无效，"
                        "请把 cache/app.log 与 cache/login-debug.png 提供给开发者。"
                    )
            # login confirmed: settle for redirect cookies, then close the
            # window automatically right after the session is saved
            await page.wait_for_timeout(2500)
            log.info("login detected - saving session, window will close")
            ua = ""
            for p in context.pages:
                try:
                    ua = await p.evaluate("navigator.userAgent")
                    break
                except Exception:
                    continue
            count = await self._save_session_from_context(
                context, ua, "real-profile")
            log.info("saved nexus session from real profile (%d cookies) - "
                     "closing window", count)
            return count
        finally:
            # 登录成功后自动关闭浏览器窗口（含所有标签页）
            try:
                for p in list(context.pages):
                    await p.close()
            except Exception:
                pass
            await context.close()

    async def _save_session_from_context(self, context, ua: str,
                                         source: str) -> int:
        """Persist ONLY nexus-related cookies (privacy-first).

        Returns the number of persisted cookies."""
        nexus_domains = [
            "https://www.nexusmods.com",
            "https://users.nexusmods.com",
            "https://next.nexusmods.com",
            "https://api.nexusmods.com",
            "https://forums.nexusmods.com",
        ]
        cookies: list[dict] = []
        for d in nexus_domains:
            try:
                cookies += await context.cookies(d)
            except Exception:
                pass
        dedup = {f"{c.get('domain')}|{c.get('name')}": c for c in cookies}
        cookies = list(dedup.values())
        config.ensure_dirs()
        raw = {"cookies": cookies, "origins": []}
        config.SESSION_FILE.write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
        )
        config.SESSION_META_FILE.write_text(json.dumps({
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "user_agent": ua,
            "cookie_count": len(cookies),
            "source": source,
        }, ensure_ascii=False), encoding="utf-8")
        return len(cookies)

    # ------------------------------------------------------------- internals
    async def _verify(self, context) -> bool:
        page = None
        try:
            page = await context.new_page()
            await page.goto(self.verify_url, wait_until="domcontentloaded",
                            timeout=45_000)
            await page.wait_for_timeout(2500)
            res = await page.evaluate(IS_LOGGED_IN_JS)
            if res.get("ok"):
                return True
            # cookie-based fallback: Nexus sets sid_*/jwt_* tokens when logged in
            cookies = await context.cookies(self.verify_url)
            names = " ".join(c["name"].lower() for c in cookies)
            return bool(names) and (
                "sid_" in names or "jwt" in names or "access_token" in names
            )
        except Exception as exc:  # pragma: no cover - network hiccup path
            log.warning("session verify failed: %s", exc)
            return False
        finally:
            if page is not None:
                await page.close()