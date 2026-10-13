"""Headless settings regressions against the isolated static preview backend.

Run with a Python environment containing Playwright. NHD_TEST_BROWSER optionally
selects a Chromium executable. No real backend, user profile, MO2, or settings
file is opened. Reports/screenshots are written only below .verify/settings-ui.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".verify" / "settings-ui"
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"checks": [], "errors": [], "blocked_requests": [], "layouts": []}


class StaticOnly(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def executable(playwright):
    selected = os.environ.get("NHD_TEST_BROWSER")
    if selected:
        if not Path(selected).is_file():
            raise AssertionError("NHD_TEST_BROWSER does not name an existing executable")
        return selected
    for candidate in [
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        playwright.chromium.executable_path,
    ]:
        if Path(candidate).is_file():
            return candidate
    raise AssertionError("Set NHD_TEST_BROWSER to an installed Chromium executable")


def passed(message):
    REPORT["checks"].append(message)
    print("PASS", message, flush=True)


def idle(page):
    page.wait_for_function("!settingsSave.running && settingsSave.pending.size === 0")


def input_only(page, selector, value):
    return page.locator(selector).evaluate("""(el, value) => {
      el.value = String(value);
      el.dispatchEvent(new Event('input', {bubbles:true}));
      const pending = settingsSave.pending.get(el.id);
      return {delay: pending ? pending.due - Date.now() : null,
              disabled: el.disabled, value: el.value};
    }""", value)


def layout(page, label):
    result = page.evaluate("""() => ({
      width: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth,
      dialog: (() => {const d = document.querySelector('dialog[open]');
        return d ? {width:d.clientWidth, scroll:d.scrollWidth, right:d.getBoundingClientRect().right} : null;})()
    })""")
    assert result["document"] <= result["width"] + 1, (label, result)
    assert result["body"] <= result["width"] + 1, (label, result)
    if result["dialog"]:
        d = result["dialog"]
        assert d["scroll"] <= d["width"] + 1 and d["right"] <= result["width"] + 1, (label, result)
    REPORT["layouts"].append({"label": label, **result})


server = ThreadingHTTPServer(("127.0.0.1", 0), partial(StaticOnly, directory=str(ROOT / "nexus-dashboard")))
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_port}"
persisted_appearance = {"fontSize": 13}

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=executable(playwright))
        context = browser.new_context(viewport={"width":1366, "height":768}, device_scale_factor=1)
        def restrict(route):
            address = urlparse(route.request.url)
            if address.netloc == urlparse(BASE).netloc and address.path == '/api/wallpaper/image/qa-fixture':
                route.fulfill(content_type='image/svg+xml', body='<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450"><rect width="800" height="450" fill="#795a3b"/><path d="M0 450L800 0V450Z" fill="#315259"/></svg>')
            elif address.netloc == urlparse(BASE).netloc and route.request.method == "GET" and not address.path.startswith("/api/"):
                route.continue_()
            else:
                REPORT["blocked_requests"].append(route.request.method + " " + route.request.url)
                route.abort()
        context.route("**/*", restrict)
        page = context.new_page()
        page.on("pageerror", lambda error: REPORT["errors"].append(str(error)))
        page.route(BASE + "/?preview=1", lambda route: route.fulfill(path=str(ROOT / "nexus-dashboard/static/index.html"), content_type="text/html"))
        def preview_script(route):
            # Simulate server persistence across reload without touching disk/user settings.
            source = (ROOT / "nexus-dashboard/static/app.js").read_text(encoding="utf-8")
            marker = "boot().catch"
            assert source.count(marker) == 1
            initial = "Object.assign(previewSettings['/api/appearance/settings'], " + json.dumps(persisted_appearance) + ");\n"
            route.fulfill(body=source.replace(marker, initial + marker), content_type="application/javascript")
        page.route(BASE + "/static/app.js*", preview_script)
        page.goto(BASE + "/?preview=1")
        page.wait_for_function('document.querySelectorAll(".mod-card").length === 5 && !!document.querySelector(".mo2-name-input")')
        assert page.evaluate("PREVIEW_MODE") is True
        page.evaluate("document.body.classList.remove('preview')")
        page.evaluate("""() => {
          window.__qaRequests = []; window.__qaActivePosts = 0; window.__qaMaxPosts = 0;
          window.__qaOriginalApi = api; window.__qaHook = null;
          api = async (path, options) => {
            const request = {path, method:options?.method || 'GET', body:options?.body ? JSON.parse(options.body) : null, started:Date.now()};
            __qaRequests.push(request);
            if (request.method === 'POST') { __qaActivePosts++; __qaMaxPosts = Math.max(__qaMaxPosts, __qaActivePosts); }
            try {
              const intercepted = __qaHook?.(request, options);
              const result = await (intercepted === undefined ? __qaOriginalApi(path, options) : intercepted);
              request.result = JSON.parse(JSON.stringify(result)); return result;
            } finally {
              request.finished = Date.now(); if (request.method === 'POST') __qaActivePosts--;
            }
          };
        }""")
        page.locator('#btnSettings').click()
        assert page.locator('#btnDlDir, #btnMo2Save, #btnWallpaperSave').count() == 0
        assert page.locator('#settingsDialog button').evaluate_all("els => els.every(el => !/^保存/.test(el.textContent))")
        passed('Settings have no save buttons; controls stay in the native modal')

        # A GET begun before an edit must not replace that edit when it arrives late.
        page.evaluate("""() => {
          window.__qaHook = request => request.method === 'GET' && request.path === '/api/appearance/settings'
            ? new Promise(resolve => {window.__qaReleaseGet = resolve;}) : undefined;
          window.__qaLateGet = loadAppearance();
        }""")
        initial_font = page.locator('.mod-title a').first.evaluate('el => parseFloat(getComputedStyle(el).fontSize)')
        changed = input_only(page, '#fontSizeRange', 16)
        assert 100 <= changed['delay'] <= 150, changed
        assert page.locator('.mod-title a').first.evaluate('el => parseFloat(getComputedStyle(el).fontSize)') > initial_font
        page.evaluate("""async () => { __qaReleaseGet({ok:true,fontSize:11}); await __qaLateGet; __qaHook = null; }""")
        assert page.locator('#fontSizeRange').input_value() == '16'
        page.wait_for_function("previewSettings['/api/appearance/settings'].fontSize === 16")
        idle(page)
        passed('Late initial appearance GET preserves a newer font edit and its autosave')

        # Checkbox changes persist immediately; range previews do not wait for I/O.
        page.locator('#mo2DeleteArchive').check()
        page.wait_for_function('mo2Preview.settings.deleteAfterInstall === true')
        idle(page)
        for selector, value, expected in [
            ('#threadRange', 128, "previewSettings['/api/takeover/threads'].maxThreads === 128"),
            ('#wallpaperOpacity', 40, "wallpaperPreview.settings.opacity === .6"),
            ('#fontSizeRange', 17, "previewSettings['/api/appearance/settings'].fontSize === 17"),
        ]:
            result = input_only(page, selector, value)
            assert 100 <= result['delay'] <= 150, (selector, result)
            assert not result['disabled']
            if selector == '#threadRange': assert page.locator('#threadValue').inner_text() == '128'
            if selector == '#wallpaperOpacity':
                assert page.evaluate("document.documentElement.style.getPropertyValue('--wallpaper-opacity')") == '0.6'
            if selector == '#fontSizeRange': assert page.locator('#fontSizeValue').inner_text() == '17'
            page.wait_for_function(expected)
            idle(page)
        passed('Checkboxes autosave immediately; all three sliders preview immediately and debounce at 150 ms')

        text_input = input_only(page, '#mo2Path', r'X:\mo2-debounced-fixture')
        assert 500 <= text_input['delay'] <= 550, text_input
        page.wait_for_function("mo2Preview.settings.mo2Path === 'X:\\\\mo2-debounced-fixture'")
        idle(page)
        for event in ['change', 'blur', 'Enter', 'close']:
            value = 'X:\\download-' + event
            page.locator('#dlDirInput').focus()
            result = input_only(page, '#dlDirInput', value)
            assert result['delay'] >= 500
            if event == 'Enter': page.locator('#dlDirInput').press('Enter')
            elif event == 'close': page.locator('#settingsDialog').evaluate('el => el.close()')
            else: page.locator('#dlDirInput').dispatch_event(event)
            page.wait_for_function("value => previewSettings['/api/download/dir'].dir === value", arg=value, timeout=450)
            idle(page)
            if event == 'close': page.locator('#btnSettings').click()
        passed('Text input debounces at 550 ms; change, blur, Enter and dialog close flush pending values')

        # Cancelling an IME composition can leave the value unchanged; it must still save.
        ime_value = 'X:\\ime-cancelled-fixture'
        input_only(page, '#mo2Path', ime_value)
        page.locator('#mo2Path').evaluate("el => el.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true,data:''}))")
        assert not page.evaluate("settingsSave.pending.has('mo2Path')")
        page.locator('#mo2Path').evaluate("el => el.dispatchEvent(new CompositionEvent('compositionend', {bubbles:true,data:''}))")
        assert page.evaluate("settingsSave.pending.has('mo2Path')")
        page.wait_for_function('value => mo2Preview.settings.mo2Path === value', arg=ime_value, timeout=2000)
        idle(page)
        assert page.evaluate("value => __qaRequests.filter(r => r.body?.mo2Path === value).length", ime_value) == 1
        passed('Cancelled IME composition with unchanged text requeues and saves the pending path exactly once')

        # Hold the first POST after the fake server applies it, then edit the same field again.
        page.evaluate("""() => {
          window.__qaHoldUsed = false;
          __qaHook = (request, options) => {
            if (request.path !== '/api/wallpaper/settings' || request.method !== 'POST' || !('opacity' in request.body) || __qaHoldUsed) return;
            __qaHoldUsed = true;
            return __qaOriginalApi(request.path, options).then(result => new Promise(resolve => {window.__qaReleasePost = () => resolve(result);}));
          };
        }""")
        input_only(page, '#wallpaperOpacity', 30)
        page.wait_for_function('typeof __qaReleasePost === "function" && settingsSave.running')
        input_only(page, '#wallpaperOpacity', 20)
        assert page.evaluate("document.documentElement.style.getPropertyValue('--wallpaper-opacity')") == '0.8'
        assert not page.locator('#wallpaperOpacity').is_disabled()
        page.evaluate('__qaReleasePost()')
        assert page.locator('#wallpaperOpacity').input_value() == '20'
        page.wait_for_function('wallpaperPreview.settings.opacity === .8 && !settingsSave.running && !settingsSave.pending.size')
        assert page.locator('#wallpaperOpacity').input_value() == '20'
        assert page.evaluate('__qaMaxPosts') == 1
        page.evaluate('__qaHook = null')
        passed('Concurrent edits serialize POSTs and save the latest value without stale response rollback')

        # A late wallpaper GET is also ignored after a new POST completes.
        page.evaluate("""() => {
          window.__qaOldWallpaper = {...wallpaperPreview.settings};
          __qaHook = request => request.method === 'GET' && request.path === '/api/wallpaper/settings'
            ? new Promise(resolve => {window.__qaReleaseWallpaperGet = resolve;}) : undefined;
          window.__qaLateWallpaper = loadWallpaperSettings();
        }""")
        input_only(page, '#wallpaperOpacity', 25)
        page.wait_for_function('wallpaperPreview.settings.opacity === .75 && !settingsSave.running')
        page.evaluate("""async () => {__qaReleaseWallpaperGet(__qaOldWallpaper); await __qaLateWallpaper; __qaHook = null;}""")
        assert page.evaluate('wallpaperState.settings.opacity') == .75
        assert page.locator('#wallpaperOpacity').input_value() == '25'
        passed('Late wallpaper settings GET cannot roll back a completed autosave')

        input_only(page, '#wallpaperDir', 'relative-invalid-path')
        page.locator('#wallpaperDir').press('Enter')
        page.wait_for_function("settingsSave.errors.has('wallpaperDir')")
        assert page.evaluate("__qaRequests.every(r => r.body?.directory !== 'relative-invalid-path')")
        assert page.locator('#wallpaperDir').get_attribute('aria-invalid') == 'true'
        input_only(page, '#wallpaperOpacity', 45)
        page.locator('#wallpaperEnabled').uncheck()
        page.wait_for_function('wallpaperPreview.settings.opacity === .55 && wallpaperPreview.settings.enabled === false')
        idle(page)
        assert page.locator('#wallpaperDir').input_value() == 'relative-invalid-path'
        assert not page.locator('#wallpaperOpacity').is_disabled()
        assert not page.locator('#wallpaperEnabled').is_disabled()
        assert page.evaluate("__qaRequests.every(r => r.body?.directory !== 'relative-invalid-path')")
        input_only(page, '#wallpaperDir', 'X:\\wallpaper-repaired-fixture')
        page.locator('#wallpaperDir').press('Enter')
        idle(page)
        assert not page.evaluate("settingsSave.errors.has('wallpaperDir')")
        passed('Invalid paths never POST and do not block valid checkbox/opacity updates in the same group')

        # Fixing the source directory must also retry an enable draft rejected for the old path.
        page.evaluate("""() => {
          window.__qaNeedsDirectoryRepair = true; window.__qaEnableAttempts = 0;
          __qaHook = (request, options) => {
            if (request.path !== '/api/wallpaper/settings' || request.method !== 'POST') return;
            if (request.body.enabled === true) {
              __qaEnableAttempts++;
              if (__qaNeedsDirectoryRepair) return Promise.reject(Object.assign(new Error('fixture directory unavailable'), {status:400}));
            }
            if (request.body.directory === 'X:\\\\wallpaper-new-valid-fixture') {
              return __qaOriginalApi(request.path, options).then(result => {__qaNeedsDirectoryRepair = false; return result;});
            }
          };
        }""")
        page.locator('#wallpaperEnabled').check()
        page.wait_for_function("settingsSave.errors.has('wallpaperEnabled')")
        assert page.locator('#wallpaperEnabled').is_checked()
        assert page.evaluate('wallpaperPreview.settings.enabled') is False
        assert not page.evaluate("settingsSave.pending.has('wallpaperEnabled')")
        input_only(page, '#wallpaperDir', 'X:\\wallpaper-new-valid-fixture')
        page.locator('#wallpaperDir').press('Enter')
        page.wait_for_function("wallpaperPreview.settings.enabled && !settingsSave.errors.has('wallpaperEnabled') && !settingsSave.running && !settingsSave.pending.size")
        assert page.evaluate('__qaEnableAttempts') == 2
        assert page.locator('#wallpaperEnabled').is_checked()
        assert page.evaluate('wallpaperState.settings.enabled') is True
        page.evaluate('__qaHook = null')
        passed('Fixing a rejected background directory automatically retries the pending enable draft and clears the error')

        # A transient failure retries without disabling controls or requiring another edit.
        page.evaluate("""() => {
          window.__qaFailedOnce = false;
          __qaHook = request => {
            if (request.path === '/api/takeover/threads' && request.method === 'POST' && !__qaFailedOnce) {
              __qaFailedOnce = true;
              return Promise.reject(Object.assign(new Error('isolated transient failure'), {status:503}));
            }
          };
        }""")
        input_only(page, '#threadRange', 192)
        page.wait_for_function("settingsSave.errors.has('threadRange')")
        assert not page.locator('#threadRange').is_disabled()
        assert page.evaluate("settingsSave.pending.get('threadRange').attempts") == 1
        assert page.evaluate("settingsSave.pending.get('threadRange').due - Date.now()") > 1500
        page.wait_for_function("previewSettings['/api/takeover/threads'].maxThreads === 192 && !settingsSave.errors.has('threadRange')", timeout=4500)
        idle(page)
        page.evaluate('__qaHook = null')
        passed('A transient save error retries automatically after two seconds and leaves controls editable')

        # Background and MO2 behavior must survive the settings workflow replacement.
        page.evaluate("wallpaperPreview.next = {ok:true,token:'qa-fixture',url:'/api/wallpaper/image/qa-fixture'}")
        page.locator('#wallpaperEnabled').check()
        page.evaluate('rotateWallpaper()')
        page.wait_for_function("wallpaperState.token === 'qa-fixture' && wallpaperState.loadingVersion === null")
        assert page.locator('#wallpaperBackdrop').is_visible()
        assert page.evaluate('wallpaperState.nextAt - Date.now()') > 299000
        page.keyboard.press('Escape')
        name = page.locator('.mo2-name-input')
        name.fill('玩家自定模组名称')
        page.evaluate('refreshMo2Status()')
        assert name.input_value() == '玩家自定模组名称'
        page.locator('.mo2-confirm-form button[type="submit"]').click()
        page.wait_for_function("mo2Preview.jobs[0].installName === '玩家自定模组名称' && mo2Preview.jobs[0].state === 'queued'")
        page.evaluate('state.discarded = new Set([1001]); renderList()')
        page.locator('#btnDiscardToggle').click()
        assert page.locator('.mod-card.discarded').count() == 1
        page.locator('#btnDiscardToggle').click()
        assert page.evaluate('[...state.discarded]') == [1001]
        passed('Wallpaper rotation, MO2 editable confirmation and archive records remain functional')

        # Connection changes must update queued status without disturbing another name draft.
        page.evaluate("""() => {
          mo2Preview.jobs.push({...mo2Preview.jobs[0], id:'qa-awaiting', state:'awaiting_confirmation',
            filename:'Awaiting-name-fixture.zip', installName:'', suggestedName:'推荐安装名称'});
          window.__qaQueuedJob = JSON.stringify(mo2Preview.jobs[0]);
          return refreshMo2Status();
        }""")
        draft = page.locator('.mo2-name-input')
        draft.fill('桥接切换时保留的安装名称')
        draft.evaluate("el => {el.setSelectionRange(2, 6); window.__qaDraftInput = el;}")
        for bridge, expected in [('needs_restart', '等待 MO2 连接'), ('ready', '等待 MO2 接收'), ('unavailable', '等待 MO2 连接')]:
            page.evaluate("""bridge => {
              mo2Preview.bridge = {state:bridge, message:'isolated bridge fixture'};
              return refreshMo2Status();
            }""", bridge)
            assert page.evaluate('JSON.stringify(mo2Preview.jobs[0]) === __qaQueuedJob')
            assert page.evaluate("mo2State.rows.get('preview-mo2').row.querySelector('.mo2-job-heading .chip').textContent") == '未安装'
            assert page.evaluate("mo2State.rows.get('preview-mo2').row.querySelector('.mo2-job-status').textContent") == expected
            assert page.evaluate("document.querySelector('.mo2-name-input') === __qaDraftInput")
            assert page.evaluate('document.activeElement === __qaDraftInput')
            assert draft.input_value() == '桥接切换时保留的安装名称'
            assert draft.evaluate('el => [el.selectionStart, el.selectionEnd]') == [2, 6]
        passed('An unchanged queued job stays uninstalled and follows restart, ready and disconnected bridge status')
        passed('Bridge-only polling preserves the exact confirmation input node, draft, focus and selection')

        page.evaluate("""() => {
          mo2Preview.jobs[0].state = 'installing';
          mo2Preview.bridge.state = 'ready';
          return refreshMo2Status();
        }""")
        assert page.evaluate("mo2State.rows.get('preview-mo2').row.querySelector('.mo2-job-status').textContent") == '在 MO2 中完成选项'
        page.evaluate("mo2Preview.bridge.state = 'unavailable'; refreshMo2Status()")
        assert page.evaluate("mo2State.rows.get('preview-mo2').row.querySelector('.mo2-job-heading .chip').textContent") == '安装中'
        assert page.evaluate("mo2State.rows.get('preview-mo2').row.querySelector('.mo2-job-status').textContent") == '等待 MO2 返回结果'
        assert page.evaluate("mo2State.rows.get('preview-mo2').row.parentElement.id") == 'mo2Jobs'
        passed('An installing job remains in progress after disconnect and waits for the MO2 result')

        page.evaluate("mo2Preview.jobs[0].state = 'queued'; mo2Preview.bridge.state = 'ready'; refreshMo2Status()")
        assert not page.locator('#mo2History').evaluate('el => el.open')
        page.evaluate("""() => {
          mo2Preview.jobs[0].state = 'installed';
          mo2Preview.jobs[0].installedName = '玩家自定模组名称';
          return refreshMo2Status();
        }""")
        assert page.locator('#mo2History').evaluate('el => el.open')
        assert page.locator('#mo2HistorySummary').inner_text() == '已安装 1 · 已跳过 0'
        assert page.locator('#mo2HistoryJobs .mo2-job').count() == 1
        page.locator('#mo2HistorySummary').click()
        assert not page.locator('#mo2History').evaluate('el => el.open')
        page.evaluate('refreshMo2Status()')
        page.evaluate('refreshMo2Status()')
        assert not page.locator('#mo2History').evaluate('el => el.open')
        passed('Queued-to-installed transition expands history once; manual collapse survives repeated polling')

        page.evaluate("""() => {
          mo2Preview.jobs.push({id:'qa-cancelled', state:'cancelled', filename:'Cancelled-fixture.zip'});
          return refreshMo2Status();
        }""")
        assert page.locator('#mo2HistorySummary').inner_text() == '已安装 1 · 已跳过 1'
        assert page.evaluate("mo2State.rows.get('qa-cancelled').row.querySelector('.mo2-job-heading .chip').textContent") == '已跳过'
        assert not page.locator('#mo2History').evaluate('el => el.open')
        passed('Cancelled jobs count as skipped separately from installed mods')

        page.evaluate("mo2Preview.jobs[0].cleanupError = 'fixture archive locked'; refreshMo2Status()")
        assert page.locator('#mo2History').evaluate('el => el.open')
        assert page.evaluate("mo2State.rows.get('preview-mo2').row.querySelector('.mo2-job-heading .chip').textContent") == '已安装'
        assert page.evaluate("mo2State.rows.get('preview-mo2').row.parentElement.id") == 'mo2HistoryJobs'
        assert page.get_by_text('清理失败：fixture archive locked', exact=True).is_visible()
        assert page.locator('#mo2HistorySummary').inner_text() == '已安装 1 · 已跳过 1'
        passed('Archive cleanup failure remains visibly installed and exposes the cleanup error in history')

        # Font scaling must reach list, form and button typography, including narrow layout.
        before_sizes = page.evaluate("['.mod-title a','#dlDirInput','#btnSettings'].map(s => parseFloat(getComputedStyle(document.querySelector(s)).fontSize))")
        page.locator('#btnSettings').click()
        input_only(page, '#fontSizeRange', 22)
        after_sizes = page.evaluate("['.mod-title a','#dlDirInput','#btnSettings'].map(s => parseFloat(getComputedStyle(document.querySelector(s)).fontSize))")
        assert all(after > before for before, after in zip(before_sizes, after_sizes)), (before_sizes, after_sizes)
        page.wait_for_function("previewSettings['/api/appearance/settings'].fontSize === 22")
        idle(page)
        page.keyboard.press('Escape')
        for width, height in [(1366,768), (390,844)]:
            page.set_viewport_size({'width':width, 'height':height})
            layout(page, f'font22-home-{width}')
            page.screenshot(path=str(OUT / f'font22-home-{width}.png'), full_page=True, animations='disabled')
            page.locator('#btnSettings').click()
            page.screenshot(path=str(OUT / f'font22-settings-{width}.png'), full_page=True, animations='disabled')
            layout(page, f'font22-settings-{width}')
            page.locator('#wallpaperOpacity').scroll_into_view_if_needed()
            page.screenshot(path=str(OUT / f'font22-settings-bottom-{width}.png'), full_page=True, animations='disabled')
            layout(page, f'font22-settings-bottom-{width}')
            for _ in range(3):
                page.keyboard.press('Tab')
                assert page.evaluate('document.querySelector("#settingsDialog").contains(document.activeElement)')
            page.keyboard.press('Escape')
            assert page.evaluate('document.activeElement.id') == 'btnSettings'
        passed('Font size updates list, inputs and buttons immediately; 22 px fits 1366 and 390 px layouts')

        persisted_appearance.update(page.evaluate("previewSettings['/api/appearance/settings']"))
        expected_reloaded_size = page.locator('.mod-title a').first.evaluate('el => parseFloat(getComputedStyle(el).fontSize)')
        page.reload()
        page.wait_for_function("document.querySelector('#fontSizeRange').value === '22' && document.querySelectorAll('.mod-card').length === 5")
        assert page.locator('#fontSizeValue').inner_text() == '22'
        assert page.locator('.mod-title a').first.evaluate('el => parseFloat(getComputedStyle(el).fontSize)') == expected_reloaded_size
        passed('Reload restores the font size from simulated persisted appearance settings')
        assert not REPORT['errors'], REPORT['errors']
        assert not REPORT['blocked_requests'], REPORT['blocked_requests']
        passed('No JavaScript errors, external requests or live backend mutations')
        browser.close()
finally:
    server.shutdown()
    server.server_close()
    (OUT / 'report.json').write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding='utf-8')
