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
import uuid
from urllib.parse import urlsplit

import config

log = logging.getLogger("nexus.session")


def _browser_channels(preferred: str | None = None) -> list[str]:
    """System browsers only: Edge or Chrome are both prerequisites.

    The bundled chromium is intentionally never used or downloaded.
    Override with env var NEXUS_BROWSER=msedge|chrome.
    """
    forced = (os.environ.get("NEXUS_BROWSER") or "").strip().lower()
    if forced in ("msedge", "chrome"):
        return [forced]
    channels = ["msedge", "chrome"] if sys.platform == "win32" else ["chrome"]
    if preferred in ("msedge", "chrome"):
        channels = [preferred] + [channel for channel in channels if channel != preferred]
    return channels


def _is_nexus_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        return parsed.scheme in ("http", "https") and (host == "nexusmods.com" or host.endswith(".nexusmods.com"))
    except ValueError:
        return False


def _safe_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        return parsed.scheme + "://" + parsed.netloc + parsed.path
    except ValueError:
        return "<unavailable>"


def _atomic_json(path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

IS_LOGGED_IN_JS = r"""
() => {
  const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
    && getComputedStyle(el).visibility !== 'hidden';
  const hrefs = Array.from(document.querySelectorAll('a[href]')).map(a => a.href || '');
  const hasAccountLink = hrefs.some(h => /https?:\/\/([\w-]+\.)?nexusmods\.com\/users\/myaccount(?:[/?#]|$)/i.test(h));
  const hasLoginForm = Array.from(document.querySelectorAll('input[type=password]')).some(visible);
  const hasUserInput = !!document.activeElement?.matches('input:not([type=hidden]), textarea, [contenteditable=true]');
  const text = document.body ? document.body.innerText.trim() : '';
  const challenge = /just a moment|checking your browser|verify (?:that )?you are human|sorry, you have been blocked/i.test(document.title + ' ' + text.slice(0, 2000));
  const loading = visible(document.querySelector('#history_loading'));
  const ready = document.readyState !== 'loading' && text.length > 40 && !challenge && !loading;
  return { ok: ready && hasAccountLink && !hasLoginForm, hasLoginForm, hasUserInput, ready, challenge };
}
"""


class SessionManager:
    """Owns the Playwright context built from a persisted storage state.

    flow:
      1. restore saved state and verify the login marker in the background;
      2. if verification fails, preserve state and show a retry/login message;
      3. only an explicit login action opens an interactive profile window.
    """

    def __init__(self, session_file=config.SESSION_FILE,
                 max_wait_minutes=config.MAX_LOGIN_WAIT_MINUTES,
                 login_url=config.LOGIN_URL, verify_url=config.SITE):
        self.session_file = session_file
        self.max_wait_minutes = max_wait_minutes
        self.login_url = login_url
        self.verify_url = verify_url
        self._verification_problem = "page_unready"

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
        """Launch an installed Edge/Chrome, preferring the saved session browser.

        Automation-fingerprint masking is applied so anti-bot checks
        (Cloudflare etc.) are less likely to flag the instance.
        """
        last_err: Exception | None = None
        for channel in _browser_channels(self._load_channel()):
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

    def _load_channel(self) -> str | None:
        try:
            raw = json.loads(config.SESSION_META_FILE.read_text(encoding="utf-8"))
            channel = raw.get("browser_channel")
            if channel in ("msedge", "chrome"):
                return channel
            # Older saved sessions recorded only the originating user agent.
            # Reuse that browser without forcing a fresh login after upgrade.
            user_agent = str(raw.get("user_agent") or "")
            if "Edg/" in user_agent:
                return "msedge"
            if "Chrome/" in user_agent:
                return "chrome"
            return None
        except (OSError, ValueError, AttributeError):
            return None

    async def _auth_page_state(self, page) -> dict:
        if not _is_nexus_url(str(page.url or "")):
            return {"ok": False, "ready": False, "hasLoginForm": False}
        try:
            result = await page.evaluate(IS_LOGGED_IN_JS)
            return result if isinstance(result, dict) else {"ok": False, "ready": False}
        except Exception:
            return {"ok": False, "ready": False, "hasLoginForm": False}

    async def _logged_in_anywhere(self, context) -> bool:
        for page in context.pages:
            state = await self._auth_page_state(page)
            if state.get("ok") and not state.get("hasLoginForm") and state.get("ready"):
                return True
        return False

    async def _retry_incomplete_login_pages(self, context, reload_counts) -> None:
        for page in context.pages:
            state = await self._auth_page_state(page)
            key = id(page)
            if (not _is_nexus_url(str(page.url or ""))
                    or state.get("hasLoginForm") or state.get("ready")
                    or state.get("challenge") or state.get("hasUserInput")
                    or reload_counts.get(key, 0) >= 2):
                continue
            reload_counts[key] = reload_counts.get(key, 0) + 1
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30_000)
            except Exception as exc:
                log.warning("login page retry failed: %s", type(exc).__name__)

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

        # Scanning always stays in the background. Only the explicit login
        # action may open the interactive browser/profile window.
        self._verification_problem = "page_unready"
        browser = await self._launch(playwright, headless=True)
        context = None
        verified = False
        try:
            context = await self._new_context(
                browser, storage_state=self.session_file)
            if await self._verify(context):
                verified = True
                log.info("session restored and verified (headless)")
                return browser, context
            log.warning("background verification could not confirm a ready signed-in page")
        except Exception as exc:
            log.warning("background session restore failed: %s", type(exc).__name__)
        finally:
            if not verified:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass
                try:
                    await browser.close()
                except Exception:
                    pass

        if self._verification_problem == "login_required":
            raise RuntimeError("请在仪表盘点击「登录」重新登录 N 网。")
        if self._verification_problem == "challenge":
            raise RuntimeError("N 网需要浏览器验证，请点击「登录」完成验证；原登录信息已保留。")
        raise RuntimeError("N 网页面未完整加载，请稍后重试；原登录信息已保留。")

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
                    return _is_nexus_url(p.url or "")
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
                         _safe_url(page.url or "about:blank"))
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
            log.info("real-profile window url=%s", _safe_url(page.url))

            async def logged_in_anywhere() -> bool:
                return await self._logged_in_anywhere(context)

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
                reload_counts = {}
                while time.monotonic() < deadline:
                    iters += 1
                    if await logged_in_anywhere():
                        log.info("login detected from ready Nexus page")
                        break
                    if iters % 5 == 0:
                        # Retry incomplete Nexus shells, but never navigate
                        # away from a form while the player is logging in.
                        await self._retry_incomplete_login_pages(context, reload_counts)
                    if iters % 15 == 0:
                        urls = []
                        for p in context.pages:
                            try:
                                urls.append(_safe_url(str(p.url or ""))[:120])
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
                context, ua, "real-profile", browser_channel=channel)
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
                                         source: str, browser_channel: str | None = None) -> int:
        """Persist ONLY nexus-related cookies (privacy-first).

        Returns the number of persisted cookies."""
        if not await self._logged_in_anywhere(context):
            raise RuntimeError("N 网登录尚未完成，原登录信息已保留。")
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
            except Exception as exc:
                raise RuntimeError("读取 N 网登录信息失败，原登录信息已保留。") from exc
        now = time.time()
        valid = []
        for cookie in cookies:
            domain = str(cookie.get("domain") or "").lstrip(".").lower()
            expires = cookie.get("expires", -1)
            if (domain != "nexusmods.com" and not domain.endswith(".nexusmods.com")):
                continue
            if not cookie.get("name") or not cookie.get("value"):
                continue
            if type(expires) in (int, float) and expires >= 0 and expires <= now:
                continue
            valid.append(cookie)
        dedup = {(c.get("domain"), c.get("path", "/"), c.get("name")): c for c in valid}
        cookies = list(dedup.values())
        if not any(relevant in str(cookie["name"]).lower()
                   for cookie in cookies for relevant in ("sid_", "jwt", "access_token")):
            raise RuntimeError("未取得有效的 N 网登录信息，原登录信息已保留。")
        raw = {"cookies": cookies, "origins": []}
        _atomic_json(self.session_file, raw)
        _atomic_json(config.SESSION_META_FILE, {
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "user_agent": ua,
            "cookie_count": len(cookies),
            "source": source,
            "browser_channel": browser_channel if browser_channel in ("msedge", "chrome") else None,
        })
        return len(cookies)

    # ------------------------------------------------------------- internals
    async def _verify(self, context) -> bool:
        page = None
        self._verification_problem = "page_unready"
        try:
            page = await context.new_page()
            for attempt in range(2):
                try:
                    if attempt and _is_nexus_url(str(page.url or "")):
                        await page.reload(wait_until="domcontentloaded", timeout=45_000)
                    else:
                        await page.goto(self.verify_url, wait_until="domcontentloaded", timeout=45_000)
                except Exception as exc:
                    log.warning("session navigation attempt %d failed: %s", attempt + 1, type(exc).__name__)
                    if page.is_closed():
                        return False
                    # Navigation can time out after the useful document arrived.
                    # Inspect that document before deciding whether to reload it.
                try:
                    for _ in range(6):
                        if page.is_closed():
                            return False
                        state = await self._auth_page_state(page)
                        if state.get("hasLoginForm"):
                            self._verification_problem = "login_required"
                            return False
                        if state.get("challenge"):
                            self._verification_problem = "challenge"
                        if state.get("ok") and state.get("ready"):
                            return True
                        await page.wait_for_timeout(1500)
                    if self._verification_problem == "challenge":
                        return False
                except Exception as exc:
                    log.warning("session verify attempt %d failed: %s", attempt + 1, type(exc).__name__)
            return False
        except Exception as exc:  # pragma: no cover - network hiccup path
            log.warning("session verify failed: %s", type(exc).__name__)
            return False
        finally:
            if page is not None:
                await page.close()
