"""Offline browser/session fixtures; no user browser or profile is launched."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus-dashboard"))
import config
import session_manager as sessions


READY = {"ok": True, "ready": True, "hasLoginForm": False}
LOADING = {"ok": False, "ready": False, "hasLoginForm": False}
FORM = {"ok": False, "ready": True, "hasLoginForm": True}


class Page:
    def __init__(self, states=None, url="https://www.nexusmods.com"):
        self.url = url
        self.closed = False
        self.states = list(states or [READY])
        self.goto = AsyncMock(side_effect=self.navigate)
        self.reload = AsyncMock()
        self.wait_for_timeout = AsyncMock()
        self.close = AsyncMock()

    async def navigate(self, url, **kwargs):
        self.url = url

    def is_closed(self):
        return self.closed

    async def evaluate(self, script):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


def context(page=None, cookies=None):
    page = page or Page()
    return types.SimpleNamespace(
        pages=[page], new_page=AsyncMock(return_value=page),
        cookies=AsyncMock(return_value=cookies or []),
    )


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nhd-session-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = self.root / "isolated-session.json"
        self.meta = self.root / "isolated-meta.json"
        self.session.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
        self.meta.write_text('{"browser_channel":"msedge","saved_at":"old"}', encoding="utf-8")
        self.manager = sessions.SessionManager(session_file=self.session)
        metadata = patch.object(config, "SESSION_META_FILE", self.meta)
        metadata.start()
        self.addCleanup(metadata.stop)
        environment = patch.dict(os.environ, {"NEXUS_BROWSER": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def originals(self):
        return self.session.read_bytes(), self.meta.read_bytes()

    async def test_imported_channel_is_preferred_and_explicit_override_wins(self):
        self.meta.write_text('{"browser_channel":"chrome"}', encoding="utf-8")
        launcher = AsyncMock(return_value=object())
        playwright = types.SimpleNamespace(chromium=types.SimpleNamespace(launch=launcher))
        await self.manager._launch(playwright, headless=True)
        self.assertEqual(launcher.call_args.kwargs["channel"], "chrome")
        self.assertTrue(launcher.call_args.kwargs["headless"])
        with patch.dict(os.environ, {"NEXUS_BROWSER": "msedge"}):
            self.assertEqual(sessions._browser_channels("chrome"), ["msedge"])

    async def test_legacy_session_uses_its_saved_browser_user_agent(self):
        for user_agent, expected in (("Chrome/130.0", "chrome"), ("Chrome/130.0 Edg/130.0", "msedge")):
            self.meta.write_text(json.dumps({"user_agent": user_agent}), encoding="utf-8")
            self.assertEqual(self.manager._load_channel(), expected)

    async def test_cookie_names_do_not_verify_an_incomplete_page(self):
        page = Page([LOADING])
        browser_context = context(page, [{"name": "sid_fixture", "value": "fixture"}])
        self.assertFalse(await self.manager._verify(browser_context))
        self.assertEqual(page.reload.await_count, 1)
        browser_context.cookies.assert_not_awaited()
        self.assertEqual(self.manager._verification_problem, "page_unready")
        page.close.assert_awaited_once()

    async def test_verification_waits_for_delayed_dom_without_reloading(self):
        page = Page([LOADING, LOADING, READY])
        self.assertTrue(await self.manager._verify(context(page)))
        self.assertEqual(page.wait_for_timeout.await_count, 2)
        page.reload.assert_not_awaited()

    async def test_network_navigation_failure_gets_one_retry(self):
        page = Page([LOADING] * 6 + [READY])
        page.goto.side_effect = TimeoutError()
        self.assertTrue(await self.manager._verify(context(page)))
        page.goto.assert_awaited_once()
        page.reload.assert_awaited_once()

    async def test_goto_timeout_accepts_ready_document_without_reload(self):
        page = Page([READY])
        page.goto.side_effect = TimeoutError("slow resource after committed document")
        with patch.object(page, "evaluate", wraps=page.evaluate) as evaluate:
            self.assertTrue(await self.manager._verify(context(page)))
        evaluate.assert_awaited_once()
        page.reload.assert_not_awaited()
        page.wait_for_timeout.assert_not_awaited()
        page.close.assert_awaited_once()

    async def test_reload_timeout_accepts_ready_document(self):
        page = Page([LOADING] * 6 + [READY])
        page.goto.side_effect = TimeoutError("initial document still loading")
        page.reload.side_effect = TimeoutError("retry committed but navigation timed out")
        with patch.object(page, "evaluate", wraps=page.evaluate) as evaluate:
            self.assertTrue(await self.manager._verify(context(page)))
        self.assertEqual(evaluate.await_count, 7)
        page.goto.assert_awaited_once()
        page.reload.assert_awaited_once()
        self.assertEqual(page.wait_for_timeout.await_count, 6)
        page.close.assert_awaited_once()

    async def test_navigation_failure_on_closed_page_stops_without_dom_check(self):
        page = Page([READY])
        page.closed = True
        page.goto.side_effect = RuntimeError("page closed during navigation")
        with patch.object(page, "evaluate", wraps=page.evaluate) as evaluate:
            self.assertFalse(await self.manager._verify(context(page)))
        evaluate.assert_not_awaited()
        page.reload.assert_not_awaited()
        page.wait_for_timeout.assert_not_awaited()
        page.close.assert_awaited_once()

    async def test_navigation_timeout_does_not_reload_challenge_document(self):
        page = Page([{**LOADING, "challenge":True}])
        page.goto.side_effect = TimeoutError("challenge document navigation timed out")
        self.assertFalse(await self.manager._verify(context(page)))
        self.assertEqual(self.manager._verification_problem, "challenge")
        page.reload.assert_not_awaited()
        self.assertEqual(page.wait_for_timeout.await_count, 6)

    async def test_login_form_is_never_reloaded_by_verification(self):
        page = Page([FORM])
        self.assertFalse(await self.manager._verify(context(page)))
        self.assertEqual(self.manager._verification_problem, "login_required")
        page.reload.assert_not_awaited()

    async def test_verification_waits_for_challenge_without_reloading_it(self):
        page = Page([{**LOADING, "challenge": True}])
        self.assertFalse(await self.manager._verify(context(page)))
        self.assertEqual(self.manager._verification_problem, "challenge")
        page.reload.assert_not_awaited()
        self.assertEqual(page.wait_for_timeout.await_count, 6)

    async def test_challenge_and_unready_errors_preserve_session_and_report_cause(self):
        original = self.originals()
        for problem, expected in (("challenge", "浏览器验证"), ("page_unready", "页面未完整加载")):
            browser_context = types.SimpleNamespace(close=AsyncMock())
            browser = types.SimpleNamespace(close=AsyncMock())
            async def unverified(_context):
                self.manager._verification_problem = problem
                return False
            with patch.object(self.manager, "_launch", AsyncMock(return_value=browser)) as launch, \
                    patch.object(self.manager, "_new_context", AsyncMock(return_value=browser_context)), \
                    patch.object(self.manager, "_verify", side_effect=unverified), \
                    self.assertRaisesRegex(RuntimeError, expected):
                await self.manager.get_or_create(object())
            launch.assert_awaited_once()
            self.assertTrue(launch.call_args.kwargs["headless"])
            browser_context.close.assert_awaited_once()
            browser.close.assert_awaited_once()
            self.assertEqual(self.originals(), original)

    async def test_successful_scan_keeps_only_a_headless_context_open(self):
        browser_context = types.SimpleNamespace(close=AsyncMock())
        browser = types.SimpleNamespace(close=AsyncMock())
        with patch.object(self.manager, "_launch", AsyncMock(return_value=browser)) as launch, \
                patch.object(self.manager, "_new_context", AsyncMock(return_value=browser_context)), \
                patch.object(self.manager, "_verify", AsyncMock(return_value=True)):
            self.assertEqual(await self.manager.get_or_create(object()), (browser, browser_context))
        launch.assert_awaited_once()
        self.assertTrue(launch.call_args.kwargs["headless"])
        browser_context.close.assert_not_awaited()
        browser.close.assert_not_awaited()

    async def test_restore_error_closes_background_browser_without_visible_fallback(self):
        original = self.originals()
        browser = types.SimpleNamespace(close=AsyncMock())
        with patch.object(self.manager, "_launch", AsyncMock(return_value=browser)) as launch, \
                patch.object(self.manager, "_new_context", AsyncMock(side_effect=RuntimeError("fixture load failure"))), \
                self.assertRaisesRegex(RuntimeError, "页面未完整加载"):
            await self.manager.get_or_create(object())
        launch.assert_awaited_once()
        self.assertTrue(launch.call_args.kwargs["headless"])
        browser.close.assert_awaited_once()
        self.assertEqual(self.originals(), original)

    async def test_login_recovery_is_bounded_and_preserves_forms_and_challenges(self):
        loading = Page([LOADING])
        form = Page([FORM])
        challenge = Page([{**LOADING, "challenge": True}])
        typing = Page([{**LOADING, "hasUserInput": True}])
        unrelated = Page([LOADING], "https://example.com/?nexusmods.com")
        browser_context = context()
        browser_context.pages = [loading, form, challenge, typing, unrelated]
        counts = {}
        for _ in range(4):
            await self.manager._retry_incomplete_login_pages(browser_context, counts)
        self.assertEqual(loading.reload.await_count, 2)
        for page in (form, challenge, typing, unrelated):
            page.reload.assert_not_awaited()

    async def test_unrelated_tab_does_not_confirm_nexus_login(self):
        self.assertFalse(await self.manager._logged_in_anywhere(context(Page([READY], "https://nexusmods.com.example.org"))))

    async def test_unfinished_login_does_not_overwrite_saved_session(self):
        original = self.originals()
        browser_context = context(Page([FORM]))
        with self.assertRaises(RuntimeError):
            await self.manager._save_session_from_context(browser_context, "fixture", "fixture", "chrome")
        self.assertEqual(self.originals(), original)
        browser_context.cookies.assert_not_awaited()

    async def test_empty_foreign_or_expired_cookies_do_not_replace_saved_session(self):
        original = self.originals()
        for cookies in ([], [
            {"name": "sid_fixture", "value": "fixture", "domain": "nexusmods.com.example.org"},
        ], [
            {"name": "sid_fixture", "value": "fixture", "domain": ".nexusmods.com", "expires": 1},
        ], [
            {"name": "consent", "value": "fixture", "domain": ".nexusmods.com"},
        ]):
            with self.subTest(cookies=len(cookies)), self.assertRaises(RuntimeError):
                await self.manager._save_session_from_context(context(cookies=cookies), "fixture", "fixture", "chrome")
            self.assertEqual(self.originals(), original)

    async def test_cookie_read_failure_does_not_replace_saved_session(self):
        original = self.originals()
        browser_context = context()
        browser_context.cookies.side_effect = RuntimeError("fixture read failure")
        with self.assertRaises(RuntimeError):
            await self.manager._save_session_from_context(browser_context, "fixture", "fixture", "chrome")
        self.assertEqual(self.originals(), original)

    async def test_save_uses_instance_path_filters_domains_and_preserves_channel(self):
        cookies = [
            {"name": "sid_fixture", "value": "fixture", "domain": ".nexusmods.com", "path": "/", "expires": -1},
            {"name": "sid_fixture", "value": "fixture", "domain": ".nexusmods.com", "path": "/users", "expires": -1},
            {"name": "foreign", "value": "fixture", "domain": "example.com", "path": "/"},
        ]
        wrong_path = self.root / "global-must-not-be-written.json"
        with patch.object(config, "SESSION_FILE", wrong_path):
            count = await self.manager._save_session_from_context(context(cookies=cookies), "fixture-UA", "fixture", "chrome")
        self.assertEqual(count, 2)
        self.assertFalse(wrong_path.exists())
        self.assertEqual(len(json.loads(self.session.read_text())["cookies"]), 2)
        self.assertEqual(json.loads(self.meta.read_text())["browser_channel"], "chrome")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    async def test_atomic_replace_failure_preserves_old_session(self):
        original = self.originals()
        cookies = [{"name": "sid_fixture", "value": "fixture", "domain": ".nexusmods.com"}]
        with patch.object(sessions.os, "replace", side_effect=OSError("fixture disk failure")), self.assertRaises(OSError):
            await self.manager._save_session_from_context(context(cookies=cookies), "fixture", "fixture", "chrome")
        self.assertEqual(self.originals(), original)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for the isolated DOM predicate fixture")
    async def test_dom_predicate_requires_ready_page_and_rejects_form_challenge_loading(self):
        fixture = r"""
const vm = require('vm');
const fs = require('fs');
const source = fs.readFileSync(0, 'utf8');
function check({form=false,loading=false,title='',body='An authenticated Nexus page with enough rendered content.',state='complete',link=true}={}) {
  const el = {offsetWidth:100, offsetHeight:20,getClientRects:()=>[{}]};
  return vm.runInNewContext('('+source+')()', {
    document:{readyState:state,title,body:{innerText:body},
      querySelector:s=>s==='#history_loading' && loading ? el:null,
      querySelectorAll:s=>s==='a[href]' ? (link?[{...el,href:'https://www.nexusmods.com/users/myaccount'}]:[]) : (form?[el]:[])},
    getComputedStyle:()=>({visibility:'visible'})
  });
}
const results = [check(),check({form:true}),check({loading:true}),check({title:'Just a moment'}),check({body:''}),check({state:'loading'}),check({link:false})];
if (results.map(x=>x.ok).join() !== 'true,false,false,false,false,false,false') process.exit(1);
"""
        subprocess.run([shutil.which("node"), "-e", fixture], input=sessions.IS_LOGGED_IN_JS,
                       text=True, capture_output=True, check=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    unittest.main()
