"""Login/refresh scheduling with fake browsers and temporary application data."""

import asyncio
from contextlib import ExitStack, asynccontextmanager
import importlib.util
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "nexus-dashboard"
sys.path.insert(0, str(DASHBOARD_DIR))
import config


class RefreshLifecycleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.scope = ExitStack()
        cls.addClassCleanup(cls.scope.close)
        cls.root = Path(cls.scope.enter_context(tempfile.TemporaryDirectory()))
        cache = cls.root / "cache" / "nexus-dashboard"
        paths = {
            "REPO_ROOT": cls.root,
            "CACHE_DIR": cache,
            "MODS_CACHE_DIR": cache / "mods",
            "PIP_CACHE_DIR": cache / "pip",
            "SESSION_FILE": cache / "session.json",
            "SESSION_META_FILE": cache / "session.meta.json",
            "SCAN_CACHE_FILE": cache / "history-scan.json",
            "DISCARD_FILE": cache / "discarded.json",
            "DEMO_DATA_FILE": cache / "demo_mods.json",
        }
        for name, value in paths.items():
            cls.scope.enter_context(patch.object(config, name, value))
        # Load a separate module only after redirecting every writable path.
        # No FastAPI lifespan, real browser, downloader or MO2 monitor starts.
        spec = importlib.util.spec_from_file_location(
            "_refresh_lifecycle_dashboard", DASHBOARD_DIR / "app.py")
        cls.dashboard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.dashboard)
        handler = getattr(cls.dashboard, "_fh", None)
        if handler is not None:
            cls.scope.callback(handler.close)
            cls.scope.callback(logging.getLogger().removeHandler, handler)

    async def asyncSetUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.state = self.dashboard.AppState()
        self.previous_mods = [{"mod_id": 42, "name": "Previously collected"}]
        self.state.mods = self.previous_mods
        self.state.last_refresh = "previous refresh"
        self.state.refresh_version = 7
        self.state.filter_stats = {"kept": 1}
        self.patches.enter_context(patch.object(self.dashboard, "state", self.state))
        self.patches.enter_context(patch.object(self.dashboard.log, "exception"))

        self.login_started = asyncio.Event()
        self.login_release = asyncio.Event()
        self.login_finished = asyncio.Event()
        self.login_error = None
        self.login_calls = 0
        self.login_cancelled = False
        self.scan_started = [asyncio.Event() for _ in range(4)]
        self.scan_release = [asyncio.Event() for _ in range(4)]
        for event in self.scan_release:
            event.set()
        self.scan_errors = {}
        self.scan_forces = []
        self.scan_cancelled = []
        self.active_scans = 0
        self.max_active_scans = 0
        self.playwright_entries = 0

        @asynccontextmanager
        async def fake_playwright():
            self.playwright_entries += 1
            yield object()

        async def fake_login(_playwright):
            self.login_calls += 1
            self.login_started.set()
            try:
                await self.login_release.wait()
                if self.login_error:
                    raise self.login_error
                return 2
            except asyncio.CancelledError:
                self.login_cancelled = True
                raise
            finally:
                self.login_finished.set()

        async def fake_scan(_playwright, *, force_refresh=False):
            index = len(self.scan_forces)
            self.scan_forces.append(force_refresh)
            self.active_scans += 1
            self.max_active_scans = max(self.max_active_scans, self.active_scans)
            self.scan_started[index].set()
            try:
                await self.scan_release[index].wait()
                if index in self.scan_errors:
                    raise self.scan_errors[index]
                return [{"mod_id": 100 + index, "name": "Fresh result"}]
            except asyncio.CancelledError:
                self.scan_cancelled.append(index)
                raise
            finally:
                self.active_scans -= 1

        self.patches.enter_context(patch.object(
            self.dashboard, "async_playwright", fake_playwright))
        self.patches.enter_context(patch.object(
            self.dashboard, "session_manager",
            SimpleNamespace(login_with_real_profile=fake_login)))
        self.patches.enter_context(patch.object(
            self.dashboard, "runner",
            return_value=SimpleNamespace(run=fake_scan, filter_removed=0)))

        # These sentinels also catch accidental cache/archive writes on failure.
        self.protected_files = {
            config.SESSION_FILE: b'{"fixture":"saved session"}',
            config.SCAN_CACHE_FILE: b'{"items":[{"mod_id":42}]}',
            config.DISCARD_FILE: b'["skyrimspecialedition:42"]',
            config.MODS_CACHE_DIR / "42.json": b'{"archive":"preserved"}',
            self.root / "download.zip": b"downloaded package",
        }
        for path, contents in self.protected_files.items():
            path.write_bytes(contents)

    async def asyncTearDown(self):
        tasks = [task for task in (self.state.login_task, self.state.refresh_task)
                 if task is not None and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def settle(self):
        # Login may replace a completed old scan with one new forced scan.
        for _ in range(4):
            tasks = [task for task in (self.state.login_task, self.state.refresh_task)
                     if task is not None and not task.done()]
            if not tasks:
                return
            await asyncio.wait_for(asyncio.gather(*(asyncio.shield(t) for t in tasks)), 2)
        self.fail("login/refresh did not settle")

    def assert_previous_results_preserved(self):
        self.assertIs(self.state.mods, self.previous_mods)
        self.assertEqual(self.state.last_refresh, "previous refresh")
        self.assertEqual(self.state.refresh_version, 7)
        self.assertEqual(self.state.filter_stats, {"kept": 1})
        for path, contents in self.protected_files.items():
            self.assertEqual(path.read_bytes(), contents, path.name)

    async def test_repeated_login_reuses_task_and_launches_once(self):
        first = await self.dashboard.auth_profile()
        login = self.state.login_task
        await asyncio.wait_for(self.login_started.wait(), 1)
        for _ in range(3):
            reused = await self.dashboard.auth_profile()
            self.assertFalse(reused["started"])
            self.assertTrue(reused["reused"])
            self.assertIs(self.state.login_task, login)
        self.assertTrue(first["started"])
        self.assertEqual(self.login_calls, 1)
        self.assertFalse(self.login_cancelled)
        self.login_release.set()
        await self.settle()
        self.assertEqual(self.scan_forces, [True])

    async def test_retry_clears_old_error_before_scan_finishes(self):
        self.state.refresh_error = "Previous history page timed out"
        self.scan_release[0].clear()
        await self.dashboard.refresh(force=True)
        self.assertIsNone(self.state.refresh_error)
        await asyncio.wait_for(self.scan_started[0].wait(), 1)
        self.assertIsNone(self.state.refresh_error)
        self.assert_previous_results_preserved()
        self.scan_release[0].set()
        await self.settle()
        self.assertEqual(self.state.refresh_version, 8)

    async def test_refresh_during_login_waits_and_merges_requests(self):
        await self.dashboard.auth_profile()
        await asyncio.wait_for(self.login_started.wait(), 1)
        first = await self.dashboard.refresh()
        refresh = self.state.refresh_task
        reused = await self.dashboard.refresh(force=True)
        await asyncio.sleep(0)
        self.assertTrue(first["started"])
        self.assertTrue(reused["reused"])
        self.assertIs(self.state.refresh_task, refresh)
        self.assertIs(self.state.refresh_waiting_for_login, self.state.login_task)
        self.assertEqual(self.scan_forces, [])
        self.assert_previous_results_preserved()
        self.login_release.set()
        await self.settle()
        self.assertEqual(self.scan_forces, [True])
        self.assertEqual(self.max_active_scans, 1)
        self.assertEqual(self.state.refresh_version, 8)
        self.assertIsNone(self.state.refresh_waiting_for_login)

    async def test_login_finishing_before_queued_refresh_starts_does_not_deadlock(self):
        self.login_release.set()
        await self.dashboard.auth_profile()
        await self.dashboard.refresh()
        await self.settle()
        self.assertEqual(self.scan_forces, [True])
        self.assertEqual(self.state.login_status["state"], "done")

    async def test_refresh_scheduled_first_still_waits_for_new_login(self):
        await self.dashboard.refresh()
        await self.dashboard.auth_profile()
        await asyncio.wait_for(self.login_started.wait(), 1)
        self.assertEqual(self.scan_forces, [])
        self.assertIs(self.state.refresh_waiting_for_login, self.state.login_task)
        self.login_release.set()
        await self.settle()
        self.assertEqual(self.scan_forces, [True])

    async def test_successful_login_waits_for_existing_scan_then_forces_new_scan(self):
        self.scan_release[0].clear()
        await self.dashboard.refresh()
        old = self.state.refresh_task
        await asyncio.wait_for(self.scan_started[0].wait(), 1)
        # A stale progress flag is deliberately unrelated to the task lock.
        self.state.progress.state["running"] = False
        await self.dashboard.auth_profile()
        self.login_release.set()
        await asyncio.wait_for(self.login_finished.wait(), 1)
        await asyncio.sleep(0)
        self.assertFalse(self.state.login_task.done())
        self.assertIs(self.state.refresh_task, old)
        self.assertEqual(self.scan_forces, [False])
        self.assertTrue((await self.dashboard.refresh(force=True))["reused"])
        self.assertTrue((await self.dashboard.auth_profile())["reused"])
        self.scan_release[0].set()
        await self.settle()
        self.assertEqual(self.scan_forces, [False, True])
        self.assertEqual(self.max_active_scans, 1)
        self.assertEqual(self.scan_cancelled, [])
        self.assertFalse(self.login_cancelled)
        self.assertEqual(self.login_calls, 1)
        self.assertEqual(self.state.refresh_version, 9)

    async def test_failed_old_scan_does_not_drop_new_session_refresh(self):
        self.scan_release[0].clear()
        self.scan_errors[0] = RuntimeError("old page incomplete")
        await self.dashboard.refresh()
        await asyncio.wait_for(self.scan_started[0].wait(), 1)
        await self.dashboard.auth_profile()
        self.login_release.set()
        await asyncio.wait_for(self.login_finished.wait(), 1)
        self.scan_release[0].set()
        await self.settle()
        self.assertEqual(self.scan_forces, [False, True])
        self.assertEqual(self.max_active_scans, 1)
        self.assertIsNone(self.state.refresh_error)
        self.assertEqual(self.state.refresh_version, 8)

    async def test_failed_login_skips_queued_refresh_and_preserves_data(self):
        self.login_error = RuntimeError("login page incomplete")
        await self.dashboard.auth_profile()
        await asyncio.wait_for(self.login_started.wait(), 1)
        await self.dashboard.refresh(force=True)
        self.login_release.set()
        await self.settle()
        self.assertEqual(self.scan_forces, [])
        self.assertEqual(self.state.login_status["state"], "error")
        self.assertEqual(self.state.refresh_error, "login page incomplete")
        self.assert_previous_results_preserved()

    async def test_failed_login_before_queued_refresh_starts_still_skips_scan(self):
        self.login_error = RuntimeError("login page incomplete")
        self.login_release.set()
        await self.dashboard.auth_profile()
        await self.dashboard.refresh()
        await self.settle()
        self.assertEqual(self.scan_forces, [])
        self.assert_previous_results_preserved()

    async def test_immediate_login_retry_hands_waiting_refresh_to_latest_login(self):
        self.login_error = RuntimeError("first login incomplete")
        await self.dashboard.auth_profile()
        first_login = self.state.login_task

        def retry(_completed):
            self.login_error = None
            self.dashboard._start_login()

        # Reproduce a new request arriving before the queued refresh has resumed
        # from the first completed login. It must neither deadlock nor scan twice.
        first_login.add_done_callback(retry)
        await self.dashboard.refresh()
        await asyncio.wait_for(self.login_started.wait(), 1)
        self.login_release.set()
        await self.settle()
        self.assertEqual(self.login_calls, 2)
        self.assertEqual(self.scan_forces, [True])
        self.assertEqual(self.state.login_status["state"], "done")
        self.assertIsNone(self.state.refresh_error)

    async def test_failed_login_does_not_cancel_existing_scan(self):
        self.scan_release[0].clear()
        await self.dashboard.refresh()
        old = self.state.refresh_task
        await asyncio.wait_for(self.scan_started[0].wait(), 1)
        self.login_error = RuntimeError("login page incomplete")
        await self.dashboard.auth_profile()
        self.login_release.set()
        await asyncio.wait_for(asyncio.shield(self.state.login_task), 1)
        self.assertIs(self.state.refresh_task, old)
        self.assertFalse(old.done())
        self.assert_previous_results_preserved()
        self.scan_release[0].set()
        await self.settle()
        self.assertEqual(self.scan_forces, [False])
        self.assertEqual(self.scan_cancelled, [])

    async def test_failed_refresh_preserves_previous_results_and_files(self):
        self.scan_errors[0] = RuntimeError("history did not finish loading")
        await self.dashboard.refresh(force=True)
        await self.settle()
        self.assertEqual(self.state.refresh_error, "history did not finish loading")
        self.assertFalse(self.state.progress.snapshot()["running"])
        self.assert_previous_results_preserved()

    async def test_login_alone_forces_refresh_even_with_stale_running_flag(self):
        self.state.progress.state["running"] = True
        self.login_release.set()
        await self.dashboard.auth_profile()
        await self.settle()
        self.assertEqual(self.scan_forces, [True])
        self.assertFalse(self.state.progress.snapshot()["running"])


if __name__ == "__main__":
    unittest.main()
