"""Wait for Nexus history data, not merely the document's load event."""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit

import config

log = logging.getLogger("nexus.history")

HISTORY_STATE_JS = r"""
() => {
  const visible = el => !!el && el.getClientRects().length > 0 &&
    getComputedStyle(el).visibility !== 'hidden';
  const text = document.body?.innerText || '';
  const login = /(^|\.)users\.nexusmods\.com$/.test(location.hostname) &&
    /\/auth\/sign_in|\/login/.test(location.pathname);
  if (login || Array.from(document.querySelectorAll('input[type=password]')).some(visible))
    return {state:'login'};
  if (/just a moment|checking your browser|performing security verification/i.test(document.title) ||
      /performing security verification|website verifies you are not a bot|verify (?:that )?you are human|verifying you are human|sorry, you have been blocked/i.test(text))
    return {state:'challenge'};
  const table = document.querySelector('table.datatable, #DataTables_Table_0') ||
    Array.from(document.querySelectorAll('table')).find(t => /last\s*dl/i.test(t.innerText));
  const busy = visible(document.querySelector('#history_loading')) ||
    Array.from(document.querySelectorAll('.dataTables_processing')).some(visible);
  let dt = null;
  try {
    const jq = window.jQuery;
    if (table && jq?.fn?.dataTable?.isDataTable(table)) {
      const api = jq(table).DataTable(), info = api.page.info(), settings = api.settings()[0];
      dt = {...info, draw:settings.iDraw, initialized:!!settings._bInitComplete,
        busy:!!settings.bDrawing};
    }
  } catch (_) {}
  const rows = table ? Array.from(table.querySelectorAll('tbody tr')) : [];
  const links = table ? Array.from(table.querySelectorAll('tbody a[href]'))
    .filter(a => /\/[a-z0-9_-]+\/mods\/\d+/i.test(a.href)) : [];
  const fingerprint = rows.map(row => row.innerText).join('\n');
  const pending = busy || (dt && (!dt.initialized || dt.busy));
  const empty = !pending && dt && dt.recordsDisplay === 0 &&
    /no downloads found|no data available|no matching records/i.test(table.innerText);
  return {state:pending ? 'loading' : links.length ? 'ready' : empty ? 'empty' : 'loading',
    dt, rows:rows.length, fingerprint};
}
"""

CONFIGURE_TABLE_JS = r"""
() => {
  const table = document.querySelector('table.datatable, #DataTables_Table_0');
  const jq = window.jQuery;
  if (!table || !jq?.fn?.dataTable?.isDataTable(table)) return null;
  const api = jq(table).DataTable();
  const headers = api.columns().header().toArray();
  const updated = headers.findIndex(th => /updated/i.test(th.innerText));
  api.page.len(200);
  if (updated >= 0) api.order([updated, 'desc']);
  api.page('first').draw();
  return {page:0, sorted:updated >= 0};
}
"""

NEXT_TABLE_JS = r"""
() => {
  const table = document.querySelector('table.datatable, #DataTables_Table_0');
  const jq = window.jQuery;
  if (!table || !jq?.fn?.dataTable?.isDataTable(table)) return {available:false};
  const api = jq(table).DataTable(), info = api.page.info();
  if (info.page + 1 >= info.pages) return {available:true, end:true};
  api.page(info.page + 1).draw(false);
  return {available:true, page:info.page + 1};
}
"""


class HistoryNotReady(RuntimeError):
    pass


class HistoryLoginRequired(RuntimeError):
    pass


class HistoryVerificationRequired(RuntimeError):
    pass


async def wait_history_ready(page, timeout=45, expected_page=None, previous=None):
    deadline = time.monotonic() + timeout
    while True:
        try:
            snapshot = await page.evaluate(HISTORY_STATE_JS)
        except Exception:
            # A client redirect can destroy the execution context briefly.
            if page.is_closed():
                raise HistoryNotReady("历史页面已关闭，已保留原记录") from None
            snapshot = {"state": "loading"}
        state = snapshot.get("state")
        if state == "login":
            raise HistoryLoginRequired("N 网需要重新登录，已保留原记录")
        if state == "challenge":
            raise HistoryVerificationRequired("N 网需要验证，请点击「登录」后重试；已保留原记录")
        if state in ("ready", "empty"):
            dt = snapshot.get("dt")
            page_matches = expected_page is None or (dt and dt["page"] == expected_page)
            changed = previous is None or (
                dt and previous.get("dt") and dt.get("draw") != previous["dt"].get("draw")
            ) or snapshot.get("fingerprint") != previous.get("fingerprint")
            if page_matches and changed:
                return snapshot
        if time.monotonic() >= deadline:
            raise HistoryNotReady("历史页面未加载完成，已保留原记录")
        await asyncio.sleep(0.25)


async def open_history(page, progress=None, attempts=3):
    for attempt in range(attempts):
        if progress:
            await progress.mark("加载历史" if not attempt else f"重试加载 {attempt}/{attempts - 1}")
        try:
            location = urlsplit(str(page.url))
            host = location.hostname or ""
            same_document = (host == "nexusmods.com" or host.endswith(".nexusmods.com")) and (
                location.path == urlsplit(config.HISTORY_URL).path)
            if attempt and same_document:
                await page.reload(wait_until="domcontentloaded", timeout=45_000)
            else:
                await page.goto(config.HISTORY_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception:
            # A slow resource may time out after the useful document arrived.
            if page.is_closed():
                raise HistoryNotReady("历史页面已关闭，已保留原记录") from None
        try:
            return await wait_history_ready(page)
        except HistoryNotReady:
            if attempt + 1 == attempts:
                raise HistoryNotReady("历史页面加载失败，已自动重试并保留原记录；请稍后重试") from None
            log.warning("history document incomplete; retry %d/%d", attempt + 1, attempts - 1)


async def configure_history_table(page, snapshot):
    if snapshot["state"] == "empty" or not snapshot.get("dt"):
        return snapshot
    configured = await page.evaluate(CONFIGURE_TABLE_JS)
    if configured is None:
        raise HistoryNotReady("历史表格尚未初始化，已保留原记录")
    result = await wait_history_ready(page, expected_page=0, previous=snapshot)
    result["sorted"] = configured["sorted"]
    return result


async def advance_history_page(page, snapshot):
    if snapshot.get("dt"):
        result = await page.evaluate(NEXT_TABLE_JS)
        if not result.get("available"):
            raise HistoryNotReady("历史表格在扫描中发生变化，已保留原记录")
        if result.get("end"):
            return None
        return await wait_history_ready(page, expected_page=result["page"], previous=snapshot)
    # Older layouts: only an explicitly enabled next/load-more button advances.
    for button in await page.query_selector_all(
            '.paginate_button.next, [rel="next"], [class*="load-more"], [class*="loadMore"]'):
        if not await button.is_visible() or not await button.is_enabled():
            continue
        if await button.get_attribute("aria-disabled") == "true":
            continue
        if "disabled" in (await button.get_attribute("class") or "").split():
            continue
        await button.click(timeout=2000)
        return await wait_history_ready(page, previous=snapshot)
    return None
