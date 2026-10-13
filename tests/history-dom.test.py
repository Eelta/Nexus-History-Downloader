"""Isolated headless checks using the site's actual DataTables 1.10.19 API.

Local vendor fixtures: .verify/history-dom/vendor/{jquery,datatables}.js.
Download jquery.js from https://code.jquery.com/jquery-3.3.1.min.js and
datatables.js from https://cdn.datatables.net/1.10.19/js/jquery.dataTables.min.js.
No user profile or real website is opened; every browser request is intercepted.
"""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nexus-dashboard"))
import config
from history_page import CONFIGURE_TABLE_JS, HISTORY_STATE_JS, wait_history_ready
from scraper import NexusScraper

VENDOR = ROOT / ".verify/history-dom/vendor"
LIBRARIES = "".join("<script>" + (VENDOR / name).read_text(encoding="utf-8") + "</script>"
                    for name in ("jquery.js", "datatables.js"))
SHELL = """<!doctype html><title>History fixture</title>
<div id="history_loading">We are building your download history...</div>
<table class="datatable"><thead><tr><th>Mod name</th><th>Last DL</th>
<th>Uploader</th><th>Category</th><th>Updated</th></tr></thead><tbody></tbody></table>"""


def executable(pw):
    for candidate in (os.getenv("NHD_TEST_BROWSER", ""),
                      "C:/Program Files/Google/Chrome/Application/chrome.exe",
                      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                      pw.chromium.executable_path):
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("No isolated headless Chromium executable available")


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=executable(pw))
        try:
            context = await browser.new_context()
            # Explicitly block all external traffic, including scripts/images.
            await context.route("**/*", lambda route: route.abort())
            page = await context.new_page()
            await page.set_content(SHELL + LIBRARIES)
            state = await page.evaluate(HISTORY_STATE_JS)
            assert state["state"] == "loading" and state["dt"] is None
            assert not await page.evaluate("$.fn.dataTable.isDataTable(document.querySelector('table'))")
            await page.evaluate("$('.datatable').DataTable({data:[], language:{emptyTable:'No downloads found'}})")
            assert (await page.evaluate(HISTORY_STATE_JS))["state"] == "loading"
            await page.evaluate("document.querySelector('#history_loading').style.display='none'")
            assert (await wait_history_ready(page, timeout=1))["state"] == "empty"
            print("PASS real DOM: uninitialized/building tables are not empty history", flush=True)
            await page.evaluate("$('.datatable').DataTable().column(0).visible(false)")
            await page.evaluate(CONFIGURE_TABLE_JS)
            assert await page.evaluate("$('.datatable').DataTable().order()[0][0]") == 4
            print("PASS real DataTables: hidden columns do not shift Updated sorting", flush=True)

            await page.set_content("<title>Just a moment</title><p>Verify you are human</p>")
            assert (await page.evaluate(HISTORY_STATE_JS))["state"] == "challenge"
            await page.set_content('<input type="password"><p>Sign in</p>')
            assert (await page.evaluate(HISTORY_STATE_JS))["state"] == "login"
            print("PASS real DOM: verification and login forms are distinct", flush=True)
            await page.close()

            rows = []
            for number in range(402):
                game = "othergame" if number < 400 else "skyrimspecialedition"
                rows.append([f'<a href="https://www.nexusmods.com/{game}/mods/{number + 1}">Mod {number}</a>',
                             '2026-01-01', 'Author', 'Category', '2026-10-01'])
            body = SHELL + LIBRARIES + "<script>" + """
                window.setTimeout(() => {
                    const table = $('.datatable').DataTable({data:[]});
                    window.setTimeout(() => {
                        $('#history_loading').hide();
                        table.rows.add(ROWS).draw();
                    }, 350);
                }, 100);
            """.replace("ROWS", json.dumps(rows)) + "</script>"
            await context.unroute("**/*")

            async def serve(route):
                if route.request.url == config.HISTORY_URL:
                    await route.fulfill(status=200, content_type="text/html", body=body)
                else:
                    await route.abort()

            await context.route("**/*", serve)
            with tempfile.TemporaryDirectory(prefix="nhd-history-dom-") as temporary:
                with patch.object(config, "CACHE_DIR", Path(temporary)):
                    items = await NexusScraper(None)._collect_history(context)
            assert {item["mod_id"] for item in items} == {401, 402}, items
            assert all(item.get("cells") for item in items)
            print("PASS real DataTables: delayed render and two non-target pages reach final target page", flush=True)
            await context.close()
        finally:
            await browser.close()


asyncio.run(main())
