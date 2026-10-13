"""Offline history readiness/pagination fixtures; no browser or user data access."""
import ast
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'nexus-dashboard'))
import history_page as history
import config
import scraper


def snapshot(state='ready', *, page=0, pages=3, draw=1, fingerprint='fixture row', records=450, dt=True):
    return {'state': state, 'rows': 0 if state == 'empty' else 1,
            'fingerprint': fingerprint,
            'dt': {'page':page, 'pages':pages, 'start':page * 200,
                   'end':min((page + 1) * 200, records), 'recordsDisplay':records,
                   'draw':draw, 'initialized':True, 'busy':False} if dt else None}


class VirtualClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeButton:
    def __init__(self, page, states=(), *, visible=True, enabled=True, attributes=None):
        self.page = page
        self.states = states
        self.visible = visible
        self.enabled = enabled
        self.attributes = attributes or {}
        self.clicks = 0

    async def is_visible(self):
        return self.visible

    async def is_enabled(self):
        return self.enabled

    async def get_attribute(self, name):
        return self.attributes.get(name)

    async def click(self, **kwargs):
        self.clicks += 1
        if self.states:
            self.page.set_states(self.states)


class ScriptedHistoryPage:
    """Scripts render states and navigation outcomes without interpreting product JS."""
    def __init__(self, states=(), *, documents=None, configure=(), next_pages=()):
        self.documents = documents
        self.document_index = -1
        self.set_states(states or [snapshot('loading')])
        self.configure = deque(configure)
        self.next_pages = deque(next_pages)
        self.buttons = []
        self.calls = []
        self.closed = False
        self.url = 'about:blank'
        self.goto_error = None
        self.reload_error = None
        self.handlers = {}
        self.extracted = {}
        self.extracted_pages = []

    def set_states(self, states):
        self.states = deque(deepcopy(list(states)))

    async def evaluate(self, script, *args):
        self.calls.append(('evaluate', script))
        if script == history.HISTORY_STATE_JS:
            value = self.states[0] if len(self.states) == 1 else self.states.popleft()
            if isinstance(value, Exception):
                raise value
            self.last_snapshot = deepcopy(value)
            return deepcopy(value)
        if script == history.CONFIGURE_TABLE_JS:
            result, states = self.configure.popleft()
            if states:
                self.set_states(states)
            return deepcopy(result)
        if script == history.NEXT_TABLE_JS:
            result, states = self.next_pages.popleft()
            if states:
                self.set_states(states)
            return deepcopy(result)
        if script == 'new Date().getTimezoneOffset()':
            return -480
        # Collector integration uses the rendered page number to select fixture rows.
        if script == scraper.HISTORY_EXTRACT_JS:
            current = getattr(self, 'last_snapshot', {})
            number = (current.get('dt') or {}).get('page', 0)
            self.extracted_pages.append(number)
            return {'items':deepcopy(self.extracted.get(number, [])), 'hints':[]}
        # Diagnostic controls are intentionally empty and contain no real page data.
        return []

    async def goto(self, url, **kwargs):
        self.calls.append(('goto', url))
        self.url = url
        self.document_index = 0
        if self.documents:
            self.set_states(self.documents[0])
        if self.goto_error:
            raise self.goto_error

    async def reload(self, **kwargs):
        self.calls.append(('reload', None))
        self.document_index += 1
        if self.documents:
            self.set_states(self.documents[min(self.document_index, len(self.documents) - 1)])
        if self.reload_error:
            raise self.reload_error

    async def query_selector_all(self, selector):
        self.calls.append(('query_selector_all', selector))
        return self.buttons

    def is_closed(self):
        return self.closed

    def on(self, name, callback):
        self.handlers[name] = callback

    def remove_listener(self, name, callback):
        if self.handlers.get(name) is callback:
            del self.handlers[name]

    async def content(self):
        return '<html><body>isolated fixture</body></html>'

    async def close(self):
        self.closed = True


class HistoryReadyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = VirtualClock()
        for name, value in [('time', SimpleNamespace(monotonic=self.clock.monotonic)),
                            ('asyncio', SimpleNamespace(sleep=self.clock.sleep))]:
            override = patch.object(history, name, value)
            override.start()
            self.addCleanup(override.stop)

    async def test_late_rows_wait_until_ready(self):
        expected = snapshot(draw=4)
        page = ScriptedHistoryPage([snapshot('loading'), snapshot('loading'), expected])
        self.assertEqual(await history.wait_history_ready(page), expected)
        self.assertEqual(self.clock.sleeps, [.25, .25])

    async def test_empty_shell_reloads_then_reads_late_data(self):
        expected = snapshot(draw=2)
        page = ScriptedHistoryPage(documents=[[snapshot('loading')], [snapshot('loading'), expected]])
        progress = SimpleNamespace(mark=AsyncMock())
        self.assertEqual(await history.open_history(page, progress=progress), expected)
        self.assertEqual(sum(call[0] == 'goto' for call in page.calls), 1)
        self.assertEqual(sum(call[0] == 'reload' for call in page.calls), 1)
        self.assertEqual(progress.mark.await_count, 2)

    async def test_exhausted_shell_raises_after_bounded_reload(self):
        page = ScriptedHistoryPage([snapshot('loading')])
        with self.assertRaises(history.HistoryNotReady):
            await history.open_history(page, attempts=3)
        self.assertEqual(sum(call[0] == 'reload' for call in page.calls), 2)

    async def test_explicit_zero_records_returns_empty_without_wait(self):
        empty = snapshot('empty', records=0, pages=0, fingerprint='No downloads found')
        page = ScriptedHistoryPage([empty])
        self.assertEqual(await history.wait_history_ready(page), empty)
        self.assertEqual(self.clock.sleeps, [])

    async def test_login_and_challenge_are_distinct_and_not_automated(self):
        for state, error in [('login', history.HistoryLoginRequired), ('challenge', history.HistoryVerificationRequired)]:
            with self.subTest(state=state):
                page = ScriptedHistoryPage([{'state':state}])
                button = FakeButton(page)
                page.buttons = [button]
                with self.assertRaises(error):
                    await history.open_history(page)
                self.assertEqual(sum(call[0] == 'reload' for call in page.calls), 0)
                self.assertEqual(button.clicks, 0)

    async def test_navigation_timeout_can_still_have_ready_data(self):
        expected = snapshot()
        page = ScriptedHistoryPage([expected])
        page.goto_error = TimeoutError('isolated navigation timeout')
        self.assertEqual(await history.open_history(page), expected)
        self.assertEqual(sum(call[0] == 'reload' for call in page.calls), 0)

    async def test_navigation_not_committed_retries_url_instead_of_reloading_blank(self):
        expected = snapshot(draw=2)
        page = ScriptedHistoryPage([snapshot('loading')])
        original_goto = page.goto
        attempts = 0

        async def delayed_goto(url, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                page.calls.append(('goto', url))
                raise TimeoutError('navigation failed before the response committed')
            page.set_states([expected])
            await original_goto(url, **kwargs)

        page.goto = delayed_goto
        self.assertEqual(await history.open_history(page), expected)
        self.assertEqual(attempts, 2)
        self.assertEqual(page.url, config.HISTORY_URL)
        self.assertEqual(sum(call[0] == 'reload' for call in page.calls), 0)

    async def test_redirect_execution_context_can_recover(self):
        expected = snapshot()
        page = ScriptedHistoryPage([RuntimeError('execution context destroyed'), expected])
        self.assertEqual(await history.wait_history_ready(page), expected)
        self.assertEqual(self.clock.sleeps, [.25])

    async def test_closed_page_raises_instead_of_waiting(self):
        page = ScriptedHistoryPage([RuntimeError('closed')])
        page.closed = True
        with self.assertRaises(history.HistoryNotReady):
            await history.wait_history_ready(page)
        self.assertEqual(self.clock.sleeps, [])

    async def test_configure_waits_for_page_zero_and_new_draw(self):
        previous = snapshot(page=2, draw=5)
        final = snapshot(page=0, draw=6)
        page = ScriptedHistoryPage(configure=[({'page':0, 'sorted':True},
            [snapshot('loading'), snapshot(page=0, draw=5), final])])
        result = await history.configure_history_table(page, previous)
        self.assertEqual(result['dt']['draw'], 6)
        self.assertTrue(result['sorted'])
        self.assertEqual(len(self.clock.sleeps), 2)

    async def test_empty_and_legacy_layouts_do_not_configure_datatables(self):
        for previous in [snapshot('empty', records=0), snapshot(dt=False)]:
            with self.subTest(snapshot=previous):
                page = ScriptedHistoryPage()
                self.assertEqual(await history.configure_history_table(page, previous), previous)
                self.assertEqual(page.calls, [])

    async def test_configuration_lost_datatable_is_failure(self):
        page = ScriptedHistoryPage(configure=[(None, None)])
        with self.assertRaises(history.HistoryNotReady):
            await history.configure_history_table(page, snapshot())

    async def test_pagination_requires_expected_page_even_if_old_page_redraws(self):
        previous = snapshot(page=0, draw=3)
        final = snapshot(page=1, draw=5, fingerprint='other-game row only')
        page = ScriptedHistoryPage(next_pages=[({'available':True, 'page':1},
            [snapshot(page=0, draw=4), snapshot('loading', page=1, draw=4), final])])
        self.assertEqual(await history.advance_history_page(page, previous), final)
        self.assertEqual(len(self.clock.sleeps), 2)

    async def test_duplicate_content_on_a_new_draw_is_not_end_of_history(self):
        previous = snapshot(page=0, draw=2, fingerprint='same repeated mod row')
        final = snapshot(page=1, draw=3, fingerprint=previous['fingerprint'])
        page = ScriptedHistoryPage(next_pages=[({'available':True, 'page':1}, [final])])
        self.assertEqual(await history.advance_history_page(page, previous), final)

    async def test_only_explicit_datatables_end_returns_none(self):
        page = ScriptedHistoryPage(next_pages=[({'available':True, 'end':True}, None)])
        self.assertIsNone(await history.advance_history_page(page, snapshot(page=2, pages=3)))
        self.assertEqual(page.calls, [('evaluate', history.NEXT_TABLE_JS)])

    async def test_lost_table_and_stalled_next_page_raise(self):
        missing = ScriptedHistoryPage(next_pages=[({'available':False}, None)])
        with self.assertRaises(history.HistoryNotReady):
            await history.advance_history_page(missing, snapshot())
        stalled = ScriptedHistoryPage(next_pages=[({'available':True, 'page':1}, [snapshot()])])
        with self.assertRaises(history.HistoryNotReady):
            await history.advance_history_page(stalled, snapshot())

    async def test_legacy_next_ignores_disabled_controls_and_waits_for_new_content(self):
        previous = snapshot(dt=False)
        final = snapshot(dt=False, fingerprint='new legacy row')
        page = ScriptedHistoryPage()
        disabled = FakeButton(page, attributes={'aria-disabled':'true'})
        css_disabled = FakeButton(page, attributes={'class':'next disabled'})
        next_button = FakeButton(page, [snapshot('loading', dt=False), final])
        page.buttons = [disabled, css_disabled, next_button]
        self.assertEqual(await history.advance_history_page(page, previous), final)
        self.assertEqual([b.clicks for b in page.buttons], [0, 0, 1])


class HistorySnapshotScriptTests(unittest.TestCase):
    def test_real_snapshot_script_distinguishes_shell_empty_auth_and_other_games(self):
        node = shutil.which('node')
        if not node:
            import playwright
            candidate = Path(playwright.__file__).parent / 'driver' / ('node.exe' if sys.platform == 'win32' else 'node')
            node = str(candidate) if candidate.is_file() else None
        if not node:
            self.skipTest('Node is required for the browserless DOM snapshot fixture')
        harness = r'''
const vm = require('node:vm');
let input = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', s => input += s);
process.stdin.on('end', () => {
  const source = JSON.parse(input).source;
  const cases = [
    {name:'building', initialized:false, loading:true, records:0, text:'Building your download history', expected:'loading'},
    {name:'late-rows', initialized:true, records:20, text:'Last DL Updated', expected:'loading'},
    {name:'empty', initialized:true, records:0, text:'No downloads found', expected:'empty'},
    {name:'empty-still-initializing', initialized:false, records:0, text:'No downloads found', expected:'loading'},
    {name:'processing', initialized:true, records:1, links:true, processing:true, expected:'loading'},
    {name:'other-game', initialized:true, records:1, links:true, expected:'ready'},
    {name:'login', hostname:'users.nexusmods.com', pathname:'/auth/sign_in', expected:'login'},
    {name:'challenge', title:'Just a moment...', expected:'challenge'},
  ];
  const result = cases.map(c => {
    const element = {getClientRects:() => [1]};
    const table = {...element, innerText:c.text || 'Other game Last DL Updated',
      querySelectorAll:selector => selector === 'tbody tr' ? (c.links ? [{innerText:'Other game row'}] : [])
        : selector === 'tbody a[href]' ? (c.links ? [{href:'https://www.nexusmods.com/stardewvalley/mods/77'}] : []) : []};
    const document = {title:c.title || '', body:{innerText:c.text || ''},
      querySelector:selector => selector === '#history_loading' ? (c.loading ? element : null) : table,
      querySelectorAll:selector => selector === '.dataTables_processing' ? (c.processing ? [element] : []) : selector === 'table' ? [table] : []};
    const jq = () => ({DataTable:() => ({page:{info:() => ({page:0,pages:1,start:0,end:c.records || 0,recordsDisplay:c.records || 0})},
      settings:() => [{iDraw:1,_bInitComplete:!!c.initialized,bDrawing:false}]})});
    jq.fn = {dataTable:{isDataTable:() => true}};
    const actual = vm.runInNewContext('(' + source + ')()', {document, window:{jQuery:jq},
      location:{hostname:c.hostname || 'www.nexusmods.com',pathname:c.pathname || '/users/myaccount'},
      getComputedStyle:() => ({visibility:'visible'})});
    return {name:c.name, expected:c.expected, actual:actual.state};
  });
  process.stdout.write(JSON.stringify(result));
});
'''
        result = subprocess.run([node, '-e', harness], input=json.dumps({'source':history.HISTORY_STATE_JS}),
                                capture_output=True, text=True, timeout=10, check=True)
        for case in json.loads(result.stdout):
            with self.subTest(case=case['name']):
                self.assertEqual(case['actual'], case['expected'])


def fixture_row(mod_id, *, domain='skyrimspecialedition', downloaded='1 Oct 2026', updated='12 Oct 2026'):
    cells = [f'Fixture mod {mod_id}', downloaded, 'Fixture author', 'Fixture category', updated]
    return {'domain':domain, 'modId':mod_id,
            'href':f'https://www.nexusmods.com/{domain}/mods/{mod_id}',
            'cells':cells, 'rowText':' '.join(cells)}


def paginated_history(rows):
    """Pages can contain zero new target mods while still advancing normally."""
    count = len(rows)
    states = [snapshot(page=i, pages=count, draw=i + 2, records=count * 200,
                       fingerprint=f'rendered history page {i}') for i in range(count)]
    next_pages = [({'available':True, 'page':i},
                   [dict(states[i], state='loading'), states[i]]) for i in range(1, count)]
    next_pages.append(({'available':True, 'end':True}, None))
    page = ScriptedHistoryPage([dict(states[0], dt=dict(states[0]['dt'], draw=1))],
        configure=[({'page':0, 'sorted':True}, [states[0]])], next_pages=next_pages)
    page.extracted = dict(enumerate(rows))
    return page


class HistoryCollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = VirtualClock()
        directory = tempfile.TemporaryDirectory(prefix='nhd-history-fixture-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        replacements = [
            (history, 'time', SimpleNamespace(monotonic=self.clock.monotonic)),
            (history, 'asyncio', SimpleNamespace(sleep=self.clock.sleep)),
            (scraper, 'TZ_OFFSET_MIN', 0),
            (config, 'CACHE_DIR', self.directory),
            (config, 'SCAN_CACHE_FILE', self.directory / 'history-scan.json'),
            (config, 'DISCARD_FILE', self.directory / 'discarded.json'),
            (config, 'ensure_dirs', lambda: self.directory.mkdir(exist_ok=True)),
        ]
        for module, name, value in replacements:
            override = patch.object(module, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.old_scan = json.dumps({'app_version':config.APP_VERSION, 'game':config.TARGET_GAME,
            'timezone_offset_min':0, 'items':[{'domain':config.TARGET_GAME, 'mod_id':7}]}).encode()
        self.old_archive = b'{"skyrimspecialedition/7": true}'
        config.SCAN_CACHE_FILE.write_bytes(self.old_scan)
        config.DISCARD_FILE.write_bytes(self.old_archive)

    def collector(self, page):
        browser = SimpleNamespace(close=AsyncMock())
        context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        session = SimpleNamespace(get_or_create=AsyncMock(return_value=(browser, context)))
        return scraper.NexusScraper(session, max_history=None), browser, context

    async def publish_refresh(self, collector):
        """Execute the real refresh function without app import/startup side effects."""
        app_path = ROOT / 'nexus-dashboard' / 'app.py'
        function = next(node for node in ast.parse(app_path.read_text(encoding='utf-8')).body
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'refresh_job')
        state = SimpleNamespace(mods=[{'mod_id':7, 'name':'Previously published mod'}],
            refresh_version=7, last_refresh='previous successful refresh',
            filter_stats={'game':config.TARGET_GAME, 'kept':1, 'removed_old_downloads':0},
            refresh_error=None, refresh_task=None, progress=collector.progress)
        previous = {name:deepcopy(getattr(state, name))
                    for name in ['mods', 'refresh_version', 'last_refresh', 'filter_stats']}

        @asynccontextmanager
        async def isolated_playwright():
            yield object()

        namespace = {'asyncio':asyncio, 'state':state, 'async_playwright':isolated_playwright,
            'runner':lambda:collector, 'config':config, 'log':MagicMock(),
            'time':SimpleNamespace(strftime=lambda _: 'new successful refresh')}
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        exec(compile(module, str(app_path), 'exec'), namespace)
        await namespace['refresh_job'](force=True)
        return state, previous

    async def test_non_target_pages_are_scanned_before_target_game(self):
        page = paginated_history([[fixture_row(1, domain='stardewvalley')],
                                  [fixture_row(2, domain='cyberpunk2077')], [fixture_row(42)]])
        collector, _, context = self.collector(page)
        items = await collector._collect_history(context)
        self.assertEqual([item['mod_id'] for item in items], [42])
        self.assertEqual(page.extracted_pages, [0, 1, 2])
        self.assertTrue(page.closed)
        self.assertNotIn('response', page.handlers)

    async def test_duplicate_and_other_game_pages_do_not_hide_later_mods(self):
        page = paginated_history([[fixture_row(42)], [fixture_row(42)],
            [fixture_row(1, domain='stardewvalley')], [fixture_row(99)]])
        collector, _, context = self.collector(page)
        items = await collector._collect_history(context)
        self.assertEqual({item['mod_id'] for item in items}, {42, 99})
        self.assertEqual(page.extracted_pages, [0, 1, 2, 3])

    async def test_completed_other_game_history_has_specific_error_and_keeps_cache(self):
        page = paginated_history([[fixture_row(1, domain='stardewvalley')],
                                  [fixture_row(2, domain='cyberpunk2077')]])
        collector, browser, _ = self.collector(page)
        with self.assertRaisesRegex(RuntimeError, '历史已加载.*没有游戏'):
            await collector.run(object(), force_refresh=True)
        self.assertEqual(page.extracted_pages, [0, 1])
        self.assertEqual(config.SCAN_CACHE_FILE.read_bytes(), self.old_scan)
        self.assertEqual(config.DISCARD_FILE.read_bytes(), self.old_archive)
        browser.close.assert_awaited_once()

    async def test_incomplete_scan_never_publishes_partial_rows_or_changes_archives(self):
        for failure in ['table disappeared', 'next page stalled', 'page limit']:
            with self.subTest(failure=failure):
                page = paginated_history([[fixture_row(42)], [fixture_row(99)]])
                if failure == 'table disappeared':
                    page.next_pages = deque([({'available':False}, None)])
                elif failure == 'next page stalled':
                    page.next_pages = deque([({'available':True, 'page':1}, None)])
                collector, browser, _ = self.collector(page)
                cap = 1 if failure == 'page limit' else 250
                with patch.object(config, 'MAX_LOAD_MORE_CLICKS', cap):
                    state, previous = await self.publish_refresh(collector)
                self.assertTrue(state.refresh_error)
                for name, value in previous.items():
                    self.assertEqual(getattr(state, name), value, name)
                self.assertEqual(page.extracted_pages, [0])
                self.assertEqual(config.SCAN_CACHE_FILE.read_bytes(), self.old_scan)
                self.assertEqual(config.DISCARD_FILE.read_bytes(), self.old_archive)
                self.assertTrue(page.closed)
                self.assertNotIn('response', page.handlers)
                browser.close.assert_awaited_once()

    async def test_empty_history_does_not_replace_previous_success(self):
        page = ScriptedHistoryPage([snapshot('empty', records=0, pages=0)])
        collector, browser, _ = self.collector(page)
        state, previous = await self.publish_refresh(collector)
        self.assertIn('历史为空', state.refresh_error)
        self.assertEqual(state.mods, previous['mods'])
        self.assertEqual(state.refresh_version, previous['refresh_version'])
        self.assertEqual(config.SCAN_CACHE_FILE.read_bytes(), self.old_scan)
        self.assertEqual(config.DISCARD_FILE.read_bytes(), self.old_archive)
        self.assertEqual(page.extracted_pages, [])
        browser.close.assert_awaited_once()

    async def test_successful_history_updates_cache_only_after_all_pages(self):
        page = paginated_history([[fixture_row(42)], [fixture_row(99)]])
        collector, browser, _ = self.collector(page)
        state, previous = await self.publish_refresh(collector)
        self.assertIsNone(state.refresh_error)
        self.assertEqual({item['mod_id'] for item in state.mods}, {42, 99})
        self.assertEqual(page.extracted_pages, [0, 1])
        self.assertEqual(state.refresh_version, previous['refresh_version'] + 1)
        self.assertEqual({item['mod_id'] for item in json.loads(config.SCAN_CACHE_FILE.read_bytes())['items']}, {42, 99})
        self.assertEqual(config.DISCARD_FILE.read_bytes(), self.old_archive)
        browser.close.assert_awaited_once()

    async def test_complete_scan_with_no_outstanding_updates_publishes_empty_result(self):
        page = paginated_history([[fixture_row(42, downloaded='12 Oct 2026', updated='1 Oct 2026')]])
        collector, browser, _ = self.collector(page)
        state, previous = await self.publish_refresh(collector)
        self.assertIsNone(state.refresh_error)
        self.assertEqual(state.mods, [])
        self.assertEqual(state.filter_stats['removed_old_downloads'], 1)
        self.assertEqual(state.refresh_version, previous['refresh_version'] + 1)
        self.assertEqual(json.loads(config.SCAN_CACHE_FILE.read_bytes())['items'][0]['mod_id'], 42)
        self.assertEqual(config.DISCARD_FILE.read_bytes(), self.old_archive)
        browser.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
