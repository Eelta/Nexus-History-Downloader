"""Nexus Mods collector:
  * download-history page  -> list of (game, mod_id) the user downloaded,
  * mod description page   -> name / summary / author / last-updated,
  * mod files tab          -> main / optional / old / miscellaneous files,
  * mod changelog tab      -> per-version changelog text.

All fetching goes through the authenticated browser context; responses are
cached on disk and rate-limited so the tool stays polite to the site."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
from dateutil import parser as dt_parser
from playwright.async_api import BrowserContext

import config
from history_page import (
    HistoryNotReady, advance_history_page, configure_history_table, open_history,
)
from mo2 import atomic_json

log = logging.getLogger("nexus.scraper")


def _exc_brief(exc: Exception, limit: int = 220) -> str:
    """Short exception text WITHOUT playwright's call log (which dumps the
    full request headers incl. cookies - privacy) and without huge noise."""
    s = str(exc)
    cut = s.find("Call log:")
    if cut >= 0:
        s = s[:cut]
    s = " ".join(s.split())
    return s[:limit]

MOD_HREF_RE = re.compile(
    r"(?:nexusmods\.com/)?([a-z0-9_-]+)/mods/(\d+)", re.I
)
HISTORY_XHR_RE = re.compile(r"(history|download|myaccount|widget)", re.I)
_TIME_SUFFIX = (
    r"(?:[T\s,]+\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:\s*[APap][Mm])?"
    r"(?:\s*(?:UTC|GMT|BST|CET|CEST|EST|EDT|Z|[+-]\d{2}:?\d{2}))?)?"
)
DATE_RE = re.compile(r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}" + _TIME_SUFFIX + r")")
NUMERIC_DATE_RE = re.compile(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}" + _TIME_SUFFIX)
_DATE_TZINFOS = {"UTC": 0, "GMT": 0, "BST": 3600, "CET": 3600,
                "CEST": 7200, "EST": -18000, "EDT": -14400}
VERSION_RE = re.compile(r"v?\d+(?:\.\d+)+")
CHANGELOG_HEADER_RE = re.compile(r"^\s*(?:version\s*)?v?\d+(?:\.\d+){1,3}\b", re.I)
SIZE_RE = re.compile(r"([\d.,]+\s*(?:KB|MB|GB))\b", re.I)
FILE_ID_RE = re.compile(r"file_id=(\d+)")
LAST_UPDATED_RE = re.compile(
    r"Last updated[:\s]*(.+?)(?:\n|Created|Author|§|$)", re.I | re.S
)
CREATED_RE = re.compile(r"Created[:\s]*(.+?)(?:\n|Last updated|Author|§|$)", re.I | re.S)
AUTHOR_RE = re.compile(r"Author[:\s]*([^\n]+)", re.I)

HISTORY_EXTRACT_JS = r"""
() => {
  const hrefRe = /(?:nexusmods\.com\/|\/)([a-z0-9_-]+)\/mods\/(\d+)/i;
  const seen = new Map();
  const items = [];
  const looksHistory = (el) => !!(
    el.closest('[class*="history" i], [data-testid*="history" i], [id*="history" i]') ||
    el.closest('tr')
  );
  document.querySelectorAll('a[href]').forEach((a) => {
    const m = (a.href || '').match(hrefRe);
    if (!m) return;
    if (!looksHistory(a)) return;
    const key = m[1] + '/' + m[2];
    const row = a.closest('tr') || a.closest('li,article,div');
    // Deduplicate links within a row, never different downloads of a mod.
    const rowKeys = seen.get(row) || new Set();
    if (rowKeys.has(key)) return;
    rowKeys.add(key);
    seen.set(row, rowKeys);
    const cells = row
      ? Array.from(row.querySelectorAll('td'))
          .map((td) => (td.innerText || '').trim().slice(0, 200))
      : [];
    items.push({
      key,
      domain: m[1],
      modId: Number(m[2]),
      href: a.href,
      rowText: row ? (row.innerText || '').slice(0, 800) : '',
      cells,
    });
  });
  const hints = [];
  const bodyText = document.body ? document.body.innerText : '';
  const m1 = bodyText.match(/([\d,]+)\s+downloads?/i);
  if (m1) hints.push('downloads:' + m1[1]);
  const m2 = bodyText.match(/of\s+([\d,]+)\s+entries/i);
  if (m2) hints.push('entries:' + m2[1]);
  return { items, hints };
}
"""


# ------------------------------------------------------------------ parsing
def parse_nexus_date(text: str | None):
    """Parse nexus-style dates:
      * 2026/8/27 (current Nexus 2.0 pages)  or 2026-08-27
      * 03 May 2024, 8:07PM  or  15 Jan 2023
    """
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip(" ,;:·")
    # numeric slash/dash dates first - most current Nexus pages use them
    m = NUMERIC_DATE_RE.search(text)
    if m:
        try:
            return dt_parser.parse(m.group(0), yearfirst=True, tzinfos=_DATE_TZINFOS)
        except (dt_parser.ParserError, ValueError, OverflowError):
            return None
    m = DATE_RE.search(text)
    if m:
        try:
            return dt_parser.parse(m.group(1), fuzzy=True, tzinfos=_DATE_TZINFOS)
        except (dt_parser.ParserError, ValueError, OverflowError):
            pass
    try:
        return dt_parser.parse(text, fuzzy=True, tzinfos=_DATE_TZINFOS)
    except (dt_parser.ParserError, ValueError, OverflowError):
        return None


# 站点时间按本机时区渲染；解析出的 naive 时间用它换算成 UTC
# （在抓历史页时用浏览器 getTimezoneOffset() 实测并更新）
TZ_OFFSET_MIN = 0


def _as_utc(d):
    """Naive datetimes are Nexus-site-local; convert with the captured TZ."""
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone(timedelta(minutes=-TZ_OFFSET_MIN)))
    return d.astimezone(timezone.utc)


def parse_mod_meta(soup: BeautifulSoup, page_text: str) -> dict:
    meta: dict = {}
    og_title = soup.find("meta", attrs={"property": "og:title"})
    h1 = soup.find("h1")
    meta["name"] = (
        (h1.get_text(" ", strip=True) if h1 else "")
        or (og_title.get("content", "").strip() if og_title else "")
        or ""
    )
    # Nexus page titles sometimes carry a " - …" / " at …" suffix
    meta["name"] = re.split(r"\s+-\s+.*?Nexus| at .*?Nexus", meta["name"])[0].strip()
    for prop in ("description", "og:description"):
        tag = soup.find("meta", attrs={"name": prop, "content": True})
        if tag and tag.get("content", "").strip():
            meta["summary"] = tag["content"].strip()
            break
    # 最可靠：<time class="dst-date-adjust" data-date="epoch秒">（本站口径）
    t = soup.find("time", attrs={"class": re.compile(r"dst-date-adjust")})
    if t is not None and t.get("data-date"):
        try:
            epoch = int(str(t["data-date"]).strip())
            meta["updated_at"] = datetime.fromtimestamp(
                epoch, tz=timezone.utc).isoformat()
            meta["updated_raw"] = t.get_text(" ", strip=True)
        except (ValueError, TypeError, OverflowError):
            pass
    if "updated_at" not in meta:
        m = None
        # 避开 Cookiebot 声明横幅里的 "Last updated ... by Cookiebot"（每页都有，
        # 日期不是模组更新时间）
        for text_variant in (page_text, page_text.replace("\n", " | ")):
            for cand in LAST_UPDATED_RE.finditer(text_variant):
                seg = text_variant[max(0, cand.start() - 80): cand.end() + 100]
                if re.search(r"cookie", seg, re.I):
                    continue
                m = cand
                break
            if m:
                break
        if not m:
            flat = re.sub(r"\s+", " ", page_text)
            m = re.search(r"Last updated[:\s]*([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4}[^|]*)", flat)
        meta["updated_raw"] = m.group(1).strip().rstrip("|") if m else ""
        # keep only the date token itself (raw text may contain "on 2026/8/27 by ...")
        token = _find_date_token(meta["updated_raw"])
        if token:
            meta["updated_raw"] = token
        meta["updated_at"] = (
            _as_utc(parse_nexus_date(meta["updated_raw"])).isoformat()
            if parse_nexus_date(meta["updated_raw"]) else None
        )
    m = CREATED_RE.search(page_text)
    meta["created_raw"] = m.group(1).strip() if m else ""
    meta["created_at"] = (
        _as_utc(parse_nexus_date(meta["created_raw"])).isoformat()
        if parse_nexus_date(meta["created_raw"]) else None
    )
    # new Nexus 2.0 pages render the author right inside "Last updated on
    # 2026/8/27 by <a>AuthorName</a>" - prefer that, strip the HTML
    ma = re.search(
        r"Last updated[^<]{0,40}?by\s*(?:<[^>]*>)?\s*([^<]{2,40}?)\s*(?:<|$)",
        page_text, re.I,
    )
    if ma:
        meta["author"] = ma.group(1).strip()
    else:
        m = AUTHOR_RE.search(page_text)
        author = re.sub(r"<[^>]+>", "", m.group(1)) if m else ""
        meta["author"] = author.strip().rstrip("|")[:60] if author else ""
    meta["category"] = ""
    m = re.search(r"Category[:\s]*([^\n|]+)", re.sub(r"\s+", " ", page_text))
    if m:
        meta["category"] = re.sub(r"<[^>]+>", "", m.group(1)).strip().rstrip("|")[:60]
    return meta


def _find_date_token(row_text: str) -> str:
    """First date-looking token in a row: numeric (2026/8/27) or named
    month (03 May 2025, 8:07PM) format."""
    m = re.search(NUMERIC_DATE_RE.pattern + "|" + DATE_RE.pattern, row_text)
    return m.group(0) if m else ""


def _history_date_tokens(item: dict) -> tuple[str, str]:
    """Use the same Last DL / Updated values for filtering and display."""
    cells = item.get("cells") or []
    dl = _find_date_token(cells[1]) if len(cells) >= 2 else ""
    up = _find_date_token(cells[4]) if len(cells) >= 5 else ""
    if not cells:  # XHR-only entries may carry a download timestamp.
        dl = _find_date_token(item.get("rowText", ""))
    return dl, up


def _merge_history_item(seen: dict[str, dict], entry: dict) -> bool:
    """Keep the latest known download, regardless of row/page ordering."""
    key = f"{entry['domain']}/{entry['mod_id']}"
    old = seen.get(key)
    if old is None:
        seen[key] = entry
        return True
    old_dl = _as_utc(parse_nexus_date(_history_date_tokens(old)[0]))
    new_dl = _as_utc(parse_nexus_date(_history_date_tokens(entry)[0]))
    if new_dl and (old_dl is None or new_dl > old_dl):
        seen[key] = entry
    elif new_dl == old_dl and entry.get("cells") and not old.get("cells"):
        seen[key] = entry
    return False


def _row_fields(row_text: str) -> dict:
    size_m = SIZE_RE.search(row_text)
    # version column usually comes AFTER the file name (which often embeds a
    # version too), so prefer the LAST version-looking token in the row,
    # ignoring numbers that are part of a size ("11.5 MB" -> not a version)
    versions = [
        m for m in VERSION_RE.finditer(row_text)
        if not re.match(r"\s*(?:KB|MB|GB)\b", row_text[m.end():], re.I)
    ]
    ver = versions[-1].group(0) if versions else ""
    uploaded_raw = _find_date_token(row_text)
    return {
        "size": size_m.group(1) if size_m else "",
        "version": ver,
        "uploaded_raw": uploaded_raw,
        "uploaded_at": (
            _as_utc(parse_nexus_date(uploaded_raw)).isoformat()
            if uploaded_raw and parse_nexus_date(uploaded_raw) else None
        ),
    }


def _human_size(kb_raw) -> str:
    """Nexus gives file size in KB (int) - format like the site."""
    try:
        kb = float(kb_raw)
    except (TypeError, ValueError):
        return ""
    if kb >= 1024 * 1024:
        return f"{kb / 1024 / 1024:.1f} GB"
    if kb >= 1024:
        return f"{kb / 1024:.1f} MB"
    return f"{int(kb)} KB"


def parse_files_html(soup: BeautifulSoup, page_text: str) -> dict[str, list[dict]]:
    """Extract files grouped by category.

    Primary: Nexus 2.0 `<dt class="file-expander-header">` rows carry
    machine-readable attributes (data-name / data-version / data-size in KB /
    data-date epoch).  Falls back to the legacy row-walker for other layouts.
    """
    out = {name: [] for name in config.FILE_SECTIONS}
    seen: set[tuple] = set()

    headers = soup.find_all("dt", class_=re.compile(r"file-expander-header"))
    for dt in headers:
        name = (dt.get("data-name") or "").strip()
        version = (dt.get("data-version") or "").strip()
        size = _human_size(dt.get("data-size"))
        uploaded_at = None
        date_raw = (dt.get("data-date") or "").strip()
        if date_raw.lstrip("-").isdigit():
            try:
                uploaded_at = datetime.fromtimestamp(
                    int(date_raw), tz=timezone.utc).isoformat()
            except (ValueError, OSError, OverflowError):
                uploaded_at = None
        fid = (dt.get("data-id") or "").strip()
        # category: nearest preceding section heading (h1/h2/h3)
        cat = "Miscellaneous"
        prev_h = dt.find_previous(["h1", "h2", "h3"])
        if prev_h is not None:
            label = prev_h.get_text(" ", strip=True)
            for key in config.FILE_SECTIONS:
                if key.lower() in label.lower():
                    cat = key
                    break
        key = (cat, fid or name, version)
        if name and key not in seen:
            seen.add(key)
            out[cat].append({
                "name": name, "version": version, "size": size,
                "uploaded_raw": "", "uploaded_at": uploaded_at,
                "file_id": fid,
            })
    if any(out.values()):
        return out
    return _parse_files_html_legacy(soup, page_text)


def _parse_files_html_legacy(soup: BeautifulSoup, page_text: str) -> dict[str, list[dict]]:
    """Legacy row-walker fallback (older layouts)."""
    out = {name: [] for name in config.FILE_SECTIONS}
    seen = set()

    def add(section: str, row_text: str, fid: str = "", name: str | None = None) -> None:
        key = (section, row_text[:120])
        if key in seen:
            return
        seen.add(key)
        if len(row_text) < 3:
            return
        fields = _row_fields(row_text)
        if not name:
            first = row_text.split("\n")[0].strip()
            name = first[:100] if first else row_text[:100]
        out[section].append({**fields, "name": name, "file_id": fid})

    for section in config.FILE_SECTIONS:
        header = soup.find(string=re.compile(rf"^\s*{section}\s*$", re.I))
        if header:
            h = header.parent
            if h is not None:
                node = h.find_next()
                while node is not None:
                    if node.name in ("h1", "h2", "h3", "h4", "h5"):
                        break
                    if node.name in ("tr", "li"):
                        txt = node.get_text(" ", strip=True)
                        link = node.find("a", href=FILE_ID_RE)
                        fid = ""
                        name = None
                        if link:
                            m = FILE_ID_RE.search(link.get("href", ""))
                            fid = m.group(1) if m else ""
                            name = link.get_text(" ", strip=True) or None
                        add(section, txt, fid, name=name)
                    node = node.find_next()

    sections_flat = re.split(
        r"\n\s*(Main files|Optional files|Old files|Miscellaneous)\s*\n",
        page_text, flags=re.I)
    pos = 0
    while pos < len(sections_flat) - 1:
        title = sections_flat[pos + 1].strip()
        body = sections_flat[pos + 2]
        if title in out and not out[title]:
            for line in body.splitlines():
                line = line.strip()
                if len(line) < 8:
                    continue
                if not (FILE_ID_RE.search(line) or SIZE_RE.search(line)
                        or DATE_RE.search(line)):
                    continue
                fid_m = FILE_ID_RE.search(line)
                add(title, line, fid_m.group(1) if fid_m else "")
        pos += 3
    return out


def parse_changelog_text(page_text: str) -> list[dict]:
    """Best-effort changelog extraction from page innerText.

    A new entry starts at a line that *begins* with a version token
    ("Version 5.2", "v5.2", "5.2") or that carries both a version and a
    date on one line (e.g. "SkyUI 5.2  - 03 May 2025, 8:07PM").  A pure
    date line directly after a fresh header becomes that entry's date;
    everything else is appended as entry text.
    """
    entries: list[dict] = []
    current: dict | None = None
    seen_headers: set[str] = set()

    def append_text(line: str) -> None:
        if current is None:
            return
        merged = (current["text"] + "\n" + line).strip()
        current["text"] = merged[:4000]

    for raw in page_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        ver_m = VERSION_RE.search(line)
        if current is not None and current["date"] is None \
                and not current["text"] and re.match(DATE_RE, line):
            current["date"] = (
                _as_utc(parse_nexus_date(line)).isoformat()
                if parse_nexus_date(line) else None
            )
            continue
        is_head = bool(re.match(CHANGELOG_HEADER_RE, line)) \
            or (ver_m is not None and re.search(DATE_RE, line) is not None)
        if is_head and ver_m:
            key = ver_m.group(0)
            if key in seen_headers:
                append_text(line)
                continue
            seen_headers.add(key)
            d = parse_nexus_date(line) if re.search(DATE_RE, line) else None
            current = {
                "version": key,
                "label": line[:120],
                "date": _as_utc(d).isoformat() if d else None,
                "text": "",
            }
            entries.append(current)
            continue
        if current is not None:
            append_text(line)
    return entries


def filter_old_downloads(mods: list[dict]) -> tuple[list[dict], int]:
    """Keep only mods that have been UPDATED since the user downloaded them
    (update time >= download time).  Mods without a known download time are
    kept because they cannot be proven outdated.

    Returns (kept, removed_count).
    """
    kept: list[dict] = []
    removed = 0
    for m in mods:
        dl = _as_utc(parse_nexus_date(m.get("downloaded_at")))
        up = _as_utc(parse_nexus_date(m.get("updated_at")))
        if dl and up and dl > up:
            removed += 1
            continue
        kept.append(m)
    return kept, removed


def parse_changelog_html(soup) -> list[dict]:
    """Nexus 2.0 changelog structure:
    <ul class="change-logs"><li><h3>Version 4.6</h3><div class="log-change">
    <ul class="arrowlist"><li>bullets…</li>…"""
    entries: list[dict] = []
    for li in soup.select("ul.change-logs > li"):
        h3 = li.find(["h3", "h4", "strong"])
        label = h3.get_text(" ", strip=True) if h3 else ""
        ver_m = VERSION_RE.search(label)
        date = None
        t = li.find("time", attrs={"class": re.compile(r"dst-date-adjust")})
        if t is not None and t.get("data-date"):
            try:
                date = datetime.fromtimestamp(
                    int(str(t["data-date"]).strip()), tz=timezone.utc
                ).isoformat()
            except (ValueError, TypeError, OverflowError):
                pass
        bullets = [b.get_text(" ", strip=True)
                   for b in li.select(".log-change li")
                   if b.get_text(" ", strip=True)]
        if bullets:
            text = "\n".join(bullets)
        else:
            text = li.get_text(" ", strip=True)
        if not (ver_m or bullets or label):
            continue
        entries.append({
            "version": ver_m.group(0) if ver_m else (label or f"v{len(entries) + 1}"),
            "label": label[:120],
            "date": date,
            "text": text[:4000],
        })
    return entries


def light_filter_items(items: list[dict]) -> tuple[list[dict], int]:
    """Phase-1 filter using ONLY the history-table dates (Last DL / Updated
    columns) so mods that would be filtered out are NEVER fully crawled.

    Compare Updated with the LATEST download shown in the UI. Older downloads
    do not imply an outstanding update after a newer download. Unknown dates
    are kept conservatively.
    Returns (kept_items, removed_count).
    """
    kept: list[dict] = []
    removed = 0
    for it in items:
        dl_tok, up_tok = _history_date_tokens(it)
        dl = _as_utc(parse_nexus_date(dl_tok))
        uu = _as_utc(parse_nexus_date(up_tok))
        if dl and uu and dl > uu:
            removed += 1
            continue
        kept.append(it)
    return kept, removed


def _find_entry_dicts(obj, found: list, depth: int = 0) -> None:
    if depth > 6 or obj is None:
        return
    if isinstance(obj, dict):
        has_id = ("modId" in obj or "mod_id" in obj or "modID" in obj)
        has_name = any(k in obj for k in ("name", "title", "modName"))
        if has_id and has_name:
            found.append(obj)
            return
        for v in obj.values():
            _find_entry_dicts(v, found, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _find_entry_dicts(v, found, depth + 1)


# ----------------------------------------------------------------- progress
class ProgressTracker:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.state = {
            "running": False,
            "total": 0,
            "done": 0,
            "current": "",
            "errors": [],
            "started_at": None,
            "finished_at": None,
        }

    async def reset(self, total: int):
        async with self.lock:
            self.state = {
                "running": True, "total": total, "done": 0, "current": "",
                "errors": [], "started_at": time.time(),
                "finished_at": None, "stage": "fetching",
            }

    async def stage(self, name: str):
        """Mark the current phase AND flip running=True immediately: the
        front-end only starts polling when it sees running, and history
        collection can take minutes, so it must be set at job start."""
        async with self.lock:
            self.state["stage"] = name
            self.state["running"] = True
            if not self.state.get("started_at"):
                self.state["started_at"] = time.time()

    async def mark(self, current: str):
        """Show which mod is being worked on without counting progress."""
        async with self.lock:
            self.state["current"] = current

    async def set_progress(self, done: int, total: int):
        """历史页扫描等阶段：已抓条数/总条数（用于百分比显示）。"""
        async with self.lock:
            self.state["done"] = done
            self.state["total"] = total

    async def tick(self, current: str = "", error: str | None = None):
        async with self.lock:
            self.state["done"] += 1
            if current:
                self.state["current"] = current
            if error:
                self.state["errors"].append(error)
                self.state["errors"] = self.state["errors"][-20:]

    async def finish(self):
        async with self.lock:
            self.state["running"] = False
            self.state["finished_at"] = time.time()

    def snapshot(self) -> dict:
        return dict(self.state)


# ------------------------------------------------------------------ scraper
class NexusScraper:
    def __init__(self, session_manager, max_history=config.DEFAULT_MAX_HISTORY,
                 progress: ProgressTracker | None = None):
        self.sm = session_manager
        self.max_history = max_history
        self.progress = progress or ProgressTracker()
        self._sem: asyncio.Semaphore | None = None
        self.filter_removed = 0   # mods dropped by the update-since-download rule

    def _load_scan_cache(self):
        global TZ_OFFSET_MIN
        p = config.SCAN_CACHE_FILE
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("app_version") not in config.CACHE_COMPATIBLE_VERSIONS:
                return None
            if data.get("game") != config.TARGET_GAME:
                return None
            offset = data.get("timezone_offset_min")
            if type(offset) is not int or not -840 <= offset <= 840:
                return None
            TZ_OFFSET_MIN = offset
            # 无时间缓存：是否重扫由 force 决定（每次启动/手动刷新都强制重扫）
            return data.get("items") or None
        except Exception:
            return None

    def _save_scan_cache(self, items) -> None:
        config.ensure_dirs()
        atomic_json(config.SCAN_CACHE_FILE, {
            "app_version": config.APP_VERSION,
            "game": config.TARGET_GAME,
            "timezone_offset_min": TZ_OFFSET_MIN,
            "fetched_at": time.time(),
            "items": items,
        })

    # ------------------------------------------------------------- pipeline
    async def run(self, playwright, force_refresh: bool = False) -> list[dict]:
        """精简模式: 只产出 名称/链接/更新时间/下载时间 (全部来自历史表格
        一次扫描), 不抓文件、不抓更新日志, 也没有每模组的完整爬取阶段."""
        browser = context = None
        try:
            browser, context = await self.sm.get_or_create(playwright)
            items = self._load_scan_cache()
            if items is None or force_refresh:
                items = await self._collect_history(context)
                # 进度归一：目标条目数 = 去重后的 Skyrim 模组数（表声明总数含
                # 其它游戏/重复行，永远追不到），闭合到 100% 避免“卡在 9x%”
                n = len(items)
                await self.progress.set_progress(n or 1, n or 1)
                self._save_scan_cache(items)
            else:
                log.info("using cached history scan (%d skyrim entries)",
                         len(items))
            items, removed_light = light_filter_items(items)
            self.filter_removed = removed_light
            if removed_light:
                log.info("light filter: kept %d / removed %d",
                         len(items), removed_light)
            results = [self._slim_entry(it) for it in items]
            results.sort(
                key=lambda m: (m.get("updated_at") or "") or "0000",
                reverse=True,
            )
            await self.progress.reset(len(results))
            await self.progress.finish()
            return results
        finally:
            if browser is not None:
                await browser.close()

    def _slim_entry(self, item: dict) -> dict:
        """历史表格行 -> 精简模组条目（不发起任何额外请求）。"""
        domain, mod_id = item["domain"], item["mod_id"]
        cells = item.get("cells") or []
        name = (cells[0] if cells else "").strip() \
            or item.get("name") or f"{domain}/{mod_id}"
        dl_tok, up_tok = _history_date_tokens(item)
        up_iso = (_as_utc(parse_nexus_date(up_tok)).isoformat()
                  if up_tok and parse_nexus_date(up_tok) else None)
        dl_iso = (_as_utc(parse_nexus_date(dl_tok)).isoformat()
                  if dl_tok and parse_nexus_date(dl_tok) else None)
        return {
            "mod_id": mod_id,
            "game_domain": domain,
            "url": item.get("href")
            or f"{config.SITE}/{domain}/mods/{mod_id}",
            "name": name,
            "author": "",
            "summary": "",
            "category": "",
            "updated_raw": up_tok,
            "updated_at": up_iso,
            "created_raw": "",
            "created_at": None,
            "downloaded_at": dl_iso,
            "files": {},
            "changelog": [],
            "source": "light",
        }

    # ------------------------------------------------------- download history
    async def _collect_history(self, context: BrowserContext) -> list[dict]:
        page = await context.new_page()
        items: list[dict] = []
        seen: dict[str, dict] = {}
        xhr_found: list[dict] = []

        async def on_response(resp):
            url = resp.url
            if not HISTORY_XHR_RE.search(url):
                return
            try:
                ct = resp.headers.get("content-type", "")
                if "json" not in ct:
                    return
                body = await resp.text()
            except Exception:
                return
            try:
                payload = json.loads(body)
                found: list = []
                _find_entry_dicts(payload, found)
                for e in found:
                    mod_id = e.get("modId") or e.get("mod_id") or e.get("modID")
                    if not mod_id:
                        continue
                    domain = (e.get("domain_name") or e.get("game") or e.get("domain") or "").lower()
                    if domain != config.TARGET_GAME:
                        continue  # 只读取指定游戏的模组
                    key = f"{domain}/{mod_id}"
                    rec = {
                        "domain": domain,
                        "mod_id": int(mod_id),
                        "href": e.get("url") or "",
                        "name": (e.get("name") or e.get("title") or e.get("modName") or ""),
                        "source": "xhr",
                        "rowText": "",
                    }
                    # keep a download timestamp if the XHR payload carries one
                    for k in ("download_date", "downloaded_at", "built_on",
                              "timestamp", "date"):
                        if e.get(k) and not rec["rowText"]:
                            rec["rowText"] = str(e[k])
                    if not rec.get("rowText"):
                        rec["rowText"] = ""
                    xhr_found.append(rec)
            except Exception:
                pass

        responses = set()

        def receive_response(response):
            task = asyncio.create_task(on_response(response))
            responses.add(task)
            task.add_done_callback(responses.discard)

        page.on("response", receive_response)
        hint_entries = None
        completed = False
        try:
            snapshot = await open_history(page, self.progress)
            if snapshot["state"] == "empty":
                raise RuntimeError("下载历史为空，已保留原记录")
            global TZ_OFFSET_MIN
            TZ_OFFSET_MIN = int(await page.evaluate("new Date().getTimezoneOffset()"))
            snapshot = await configure_history_table(page, snapshot)
            sorted_by_update = snapshot.get("sorted", False)
            started = time.monotonic()
            visited = set()
            for _round in range(config.MAX_LOAD_MORE_CLICKS):
                if time.monotonic() - started > 300:
                    raise HistoryNotReady("历史扫描未完成，已保留原记录；请稍后重试")
                dt = snapshot.get("dt")
                if dt:
                    page_number = dt["page"]
                    if page_number in visited:
                        raise HistoryNotReady("历史分页未前进，已保留原记录")
                    visited.add(page_number)
                    hint_entries = dt["recordsDisplay"]
                    await self.progress.set_progress(dt["end"], max(1, hint_entries))
                result = await page.evaluate(HISTORY_EXTRACT_JS)
                for item in result.get("items") or []:
                    if item["domain"] != config.TARGET_GAME:
                        continue
                    _merge_history_item(seen, {
                        "domain": item["domain"], "mod_id": item["modId"],
                        "href": item["href"], "rowText": item.get("rowText", ""),
                        "cells": item.get("cells") or [], "source": "dom",
                    })
                if self.max_history is not None and sorted_by_update and len(seen) >= self.max_history:
                    completed = True
                    break
                snapshot = await advance_history_page(page, snapshot)
                if snapshot is None:
                    completed = True
                    break
            if not completed:
                raise HistoryNotReady("历史页数超过扫描上限，已保留原记录")
        except Exception:
            # Keep a local diagnostic for incomplete pages. It never replaces
            # the last successful scan or the player's archived mod records.
            try:
                config.ensure_dirs()
                (config.CACHE_DIR / "history-debug.html").write_text(
                    await page.content(), encoding="utf-8", errors="replace")
            except Exception:
                pass
            raise
        finally:
            page.remove_listener("response", receive_response)
            if responses:
                _, pending = await asyncio.wait(responses, timeout=2)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            await page.close()

        # Only merge additional metadata after every requested page completed.
        for rec in xhr_found:
            key = f"{rec['domain']}/{rec['mod_id']}"
            if key not in seen or not seen[key].get("cells"):
                _merge_history_item(seen, rec)
        items = list(seen.values())
        if not items:
            raise RuntimeError(
                f"历史已加载，但没有游戏「{config.TARGET_GAME}」的下载记录；已保留原记录"
            )
        # 按「模组更新时间」取最新 max_history 条（不是按下载时间）
        def _up_key(it) -> str:
            cells = it.get("cells") or []
            tok = _find_date_token(cells[4]) if len(cells) >= 5 else ""
            d = parse_nexus_date(tok)
            return _as_utc(d).isoformat() if d else ""

        items.sort(key=_up_key, reverse=True)
        items = items[: self.max_history] if self.max_history is not None else items
        log.info("collected %d %s entries%s", len(items), config.TARGET_GAME,
                 "" if self.max_history is None else f" (top {self.max_history} by UPDATE time)")
        log.info("history scan done: kept %d Skyrim mods (deduped)", len(items))
        if hint_entries and hint_entries > len(items):
            log.info("table claimed %d entries; the rest are other-game rows or "
                     "duplicate downloads collapsed by mod-level dedup",
                     hint_entries)
        return items
