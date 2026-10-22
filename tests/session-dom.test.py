"""Real browser session round trip with local fixtures; no network/profile access."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nexus-dashboard"))
import config
from history_page import HISTORY_STATE_JS
from session_manager import IS_LOGGED_IN_JS, SessionManager


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True)
        try:
            with tempfile.TemporaryDirectory(prefix="nhd-session-dom-") as temporary:
                root = Path(temporary)
                manager = SessionManager(session_file=root / "session.json")
                context = await browser.new_context()
                body = ('<title>Nexus fixture</title><a href="https://www.nexusmods.com/users/myaccount">'
                        'My account</a><p>Signed in fixture page with enough rendered content.</p>')

                async def serve(route):
                    await route.fulfill(status=200, content_type="text/html", body=body)

                await context.route("**/*", serve)
                page = await context.new_page()
                await page.goto(config.SITE)
                await page.evaluate("localStorage.setItem('session-v2', 'fixture-only')")
                await context.add_cookies([
                    {"name": "nexus_session_v2", "value": "fixture-only", "domain": ".nexusmods.com", "path": "/users"},
                    {"name": "nexus_session_v2", "value": "auth-fixture", "domain": "users.nexusmods.com", "path": "/auth"},
                    {"name": "other-site", "value": "must-not-be-saved", "domain": "example.org", "path": "/"},
                ])
                foreign = await context.new_page()
                await foreign.goto("https://example.org")
                await foreign.evaluate("localStorage.setItem('private', 'must-not-be-saved')")
                assert not await context.cookies(config.SITE), "Homepage filter should miss path-scoped auth"
                with patch.object(config, "SESSION_META_FILE", root / "session.meta.json"):
                    assert await manager._save_session_from_context(context, "fixture-UA", "fixture", "chrome") == 2
                saved = json.loads(manager.session_file.read_text())
                assert len(saved["cookies"]) == 2
                assert {c["path"] for c in saved["cookies"]} == {"/users", "/auth"}
                assert [o["origin"] for o in saved["origins"]] == [config.SITE]
                print("PASS real browser: new cookie names, all auth paths, Nexus-only storage", flush=True)

                restored = await browser.new_context(storage_state=str(manager.session_file))
                await restored.route("**/*", serve)
                restored_page = await restored.new_page()
                await restored_page.goto(config.SITE)
                assert await restored_page.evaluate("localStorage.getItem('session-v2')") == "fixture-only"
                assert len(await restored.cookies("https://users.nexusmods.com/auth/sign_in")) == 1
                assert len(await restored.cookies("https://www.nexusmods.com/users/myaccount")) == 1
                assert not await restored.cookies("https://example.org")
                await restored.close()
                print("PASS real browser: saved session restores cookies and localStorage", flush=True)

                await page.set_content('<title>www.nexusmods.com</title><a href="/users/myaccount">My account</a>'
                                       '<h1>Performing security verification</h1>'
                                       '<p>This page is displayed while the website verifies you are not a bot.</p>')
                assert (await page.evaluate(IS_LOGGED_IN_JS))["challenge"]
                assert not (await page.evaluate(IS_LOGGED_IN_JS))["ok"]
                assert (await page.evaluate(HISTORY_STATE_JS))["state"] == "challenge"
                # The unrelated fixture tab must not supply login proof.
                before = manager.session_file.read_bytes()
                with patch.object(config, "SESSION_META_FILE", root / "session.meta.json"):
                    try:
                        await manager._save_session_from_context(context, "fixture-UA", "fixture", "chrome")
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("Challenge must not replace the saved session")
                assert manager.session_file.read_bytes() == before
                assert await manager._open_login_page(context) is page
                assert len(context.pages) == 2
                assert (await page.evaluate(IS_LOGGED_IN_JS))["challenge"]
                await context.close()
                print("PASS real browser: challenge keeps session and reuses existing tab", flush=True)
        finally:
            await browser.close()


asyncio.run(main())
