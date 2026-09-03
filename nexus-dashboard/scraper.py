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
DATE_RE = re.compile(
    r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}(?:,\s*\d{1,2}:\d{2}\s*[APap][Mm])?)"
)
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
  const seen = new Set();
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
    if (seen.has(key)) return;
    seen.add(key);
    const row = a.closest('tr') || a.closest('li,article,div');
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
    text = re.sub(r"\b(UTC|GMT|BST|CET|CEST|EST|EDT)\b", "", text).strip()
    text = re.sub(r"\s+", " ", text).strip(" ,;:·")
    # numeric slash/dash dates first - most current Nexus pages use them
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = DATE_RE.search(text)
    if m:
        try:
            return dt_parser.parse(m.group(1), fuzzy=True)
        except (dt_parser.ParserError, ValueError, OverflowError):
            pass
    try:
        return dt_parser.parse(text, fuzzy=True)
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
        m2 = re.search(
            r"\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}",
            meta["updated_raw"],
        )
        if m2:
            meta["updated_raw"] = m2.group(0)
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
    m = re.search(
        r"(\d{4}[/-]\d{1,2}[/-]\d{1,2})|"
        r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}(?:[^0-9]*\d{1,2}:\d{2}\s*[APap][Mm])?)",
        row_text,
    )
    return m.group(0) if m else ""


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
        dl = m.get("downloaded_at")
        up = m.get("updated_at")
        if dl and up and str(dl) > str(up):
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

    ＜保留规则＞只要该模组有过「任意一次下载早于更新时间」，就说明存在你
    没下载过的新版本 → 保留；只有当【最早一次下载也晚于更新】（从未有新
    版本出现）时才剔除。Rows without both dates are kept.
    Returns (kept_items, removed_count).
    """
    kept: list[dict] = []
    removed = 0
    for it in items:
        cells = it.get("cells") or []
        up_tok = _find_date_token(cells[4]) if len(cells) >= 5 else ""
        u = parse_nexus_date(up_tok)
        uu = _as_utc(u) if u else None
        # 最早一次下载时间（_earliest_dl 为 ISO 字符串；缺失时退用 Last DL 列）
        dl = None
        if it.get("_earliest_dl"):
            try:
                dl = datetime.fromisoformat(
                    str(it["_earliest_dl"]).replace("Z", "+00:00"))
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=timezone.utc)
            except ValueError:
                dl = None
        if dl is None:
            dl_tok = _find_date_token(cells[1]) if len(cells) >= 2 else ""
            dd = parse_nexus_date(dl_tok)
            dl = _as_utc(dd) if dd else None
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
        p = config.SCAN_CACHE_FILE
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("app_version") != config.APP_VERSION:
                return None
            if data.get("game") != config.TARGET_GAME:
                return None
            # 无时间缓存：是否重扫由 force 决定（每次启动/手动刷新都强制重扫）
            return data.get("items") or None
        except Exception:
            return None

    def _save_scan_cache(self, items) -> None:
        config.ensure_dirs()
        config.SCAN_CACHE_FILE.write_text(json.dumps({
            "app_version": config.APP_VERSION,
            "game": config.TARGET_GAME,
            "fetched_at": time.time(),
            "items": items,
        }, ensure_ascii=False, default=str), encoding="utf-8")

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
        up_tok = _find_date_token(cells[4]) if len(cells) >= 5 else ""
        dl_tok = _find_date_token(cells[1]) if len(cells) >= 2 else ""
        if not dl_tok:
            dl_tok = _find_date_token(item.get("rowText", ""))
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
        earliest: dict[str, object] = {}   # key -> 最早一次下载时间 (datetime|None)
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
                    }
                    # keep a download timestamp if the XHR payload carries one
                    for k in ("download_date", "downloaded_at", "built_on",
                              "timestamp", "date"):
                        if e.get(k) and not rec["rowText"]:
                            rec["rowText"] = str(e[k])
                    if not rec.get("rowText"):
                        rec["rowText"] = ""
                    if key not in seen:
                        seen[key] = rec
                        xhr_found.append(rec)
            except Exception:
                pass

        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

        try:
            await page.goto(config.HISTORY_URL, wait_until="domcontentloaded",
                            timeout=60_000)
            # 实测本机时区偏移，用于把站点本地时间换算成 UTC
            global TZ_OFFSET_MIN
            try:
                TZ_OFFSET_MIN = int(await page.evaluate(
                    "new Date().getTimezoneOffset()"))
                log.info("captured browser TZ offset: %d min (UTC%+d)",
                         TZ_OFFSET_MIN, -TZ_OFFSET_MIN // 60)
            except Exception:
                pass
            # initial render
            for _ in range(3):
                await page.wait_for_timeout(1200)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(600)
            # DataTables：把每页条数调到 200（不支持再 100），减少翻页次数
            for length in ("200", "100"):
                try:
                    r = await page.evaluate(
                        "() => { try { const t = $('#DataTables_Table_0')"
                        ".DataTable(); t.page.len(" + length + ").draw(); "
                        "return 'ok'; } catch (e) { return 'err:' + e.message; } }"
                    )
                    if str(r).startswith("ok"):
                        await page.wait_for_timeout(1800)
                        break
                except Exception:
                    pass
            # 等表格真正渲染出数据（AJAX 未完成时空表会导致误判结束）
            for _ in range(12):
                res0 = await page.evaluate(HISTORY_EXTRACT_JS)
                if any(it["domain"] == config.TARGET_GAME
                       for it in (res0.get("items") or [])):
                    break
                await page.wait_for_timeout(1500)
            # 让表格按「Updated」列降序排列（等效按更新时间排序），
            # 这样只读前几页就能拿到最新更新的模组，不用翻完 17 页
            sorted_by_update = False
            try:
                await page.evaluate(r"""
                () => {
                  const ths = Array.from(document.querySelectorAll('#DataTables_Table_0 th'));
                  const th = ths.find(t => /updated/i.test(t.innerText || '')) || null;
                  if (!th) return 'no-col';
                  th.click();  // 第一次：升序
                  return 'clicked';
                }
                """)
                await page.wait_for_timeout(1500)
                desc = await page.evaluate(r"""
                () => {
                  const ths = Array.from(document.querySelectorAll('#DataTables_Table_0 th'));
                  const th = ths.find(t => /updated/i.test(t.innerText || '')) || null;
                  return th ? /sorting_desc/i.test(th.className || '') : false;
                }
                """)
                if not desc:
                    await page.evaluate(r"""
                    () => {
                      const ths = Array.from(document.querySelectorAll('#DataTables_Table_0 th'));
                      const th = ths.find(t => /updated/i.test(t.innerText || '')) || null;
                      if (th) th.click();  // 第二次：降序
                    }
                    """)
                    await page.wait_for_timeout(2500)
                sorted_by_update = True
                log.info("history table sorted by Updated desc")
            except Exception as exc:
                log.warning("could not sort by Updated column: %s",
                            _exc_brief(exc))
            # 等排序后重新渲染
            for _ in range(12):
                res0 = await page.evaluate(HISTORY_EXTRACT_JS)
                if any(it["domain"] == config.TARGET_GAME
                       for it in (res0.get("items") or [])):
                    break
                await page.wait_for_timeout(1200)
            stale = 0
            empty_pages = 0
            scan_started = time.time()
            hint_total: int | None = None
            hint_entries: int | None = None

            def absorb(res) -> int:
                """把一次提取结果并入 seen（同一模组保留“最近一次下载”的行，
                因为筛选要看最后一次下载时间），返回新增模组数。"""
                nonlocal hint_total, hint_entries
                added = 0

                def dl_time(ent):
                    c = ent.get("cells") or []
                    tok = _find_date_token(c[1]) if len(c) >= 2 else ""
                    d = parse_nexus_date(tok)
                    return _as_utc(d) if d else None

                for h in res.get("hints") or []:
                    m = re.match(r"downloads:([\d,]+)", h)
                    if m and hint_total is None:
                        hint_total = int(m.group(1).replace(",", ""))
                        log.info("history page reports %d downloads", hint_total)
                    m = re.match(r"entries:([\d,]+)", h)
                    if m and hint_entries is None:
                        hint_entries = int(m.group(1).replace(",", ""))
                        if hint_total is None:
                            hint_total = hint_entries
                        log.info("history table has %d entries total",
                                 hint_entries)
                for it in res.get("items") or []:
                    if it["domain"] != config.TARGET_GAME:
                        continue  # 只读取指定游戏的模组
                    key = f"{it['domain']}/{it['modId']}"
                    c = it.get("cells") or []
                    dl_tok = _find_date_token(c[1]) if len(c) >= 2 else ""
                    d_this = parse_nexus_date(dl_tok)
                    # 记录该模组“最早一次下载时间”：只要有一次下载早于
                    # 更新时间，就说明存在你没下载过的新版本 → 保留
                    old_earliest = earliest.get(key)
                    d_aware = _as_utc(d_this) if d_this else None
                    if old_earliest is None or (
                            d_aware and old_earliest and d_aware < old_earliest):
                        earliest[key] = d_aware if d_aware else old_earliest
                    entry = {
                        "domain": it["domain"], "mod_id": it["modId"],
                        "href": it["href"],
                        "rowText": it.get("rowText", ""),
                        "cells": c,
                        "source": "dom",
                        "_earliest_dl": (earliest[key].isoformat()
                                         if earliest[key] else ""),
                    }
                    old = seen.get(key)
                    if old is None:
                        seen[key] = entry
                        added += 1
                    else:
                        d_old = dl_time(old)
                        d_new = dl_time(entry)
                        # 展示用行：保留“最近一次下载”的那条；
                        # 筛选用的最早下载时间单独记录在 _earliest_dl
                        if d_new and (d_old is None or d_new > d_old):
                            seen[key] = entry
                items[:] = list(seen.values())
                return added

            for _round in range(config.MAX_LOAD_MORE_CLICKS):
                # 总时长护栏：分页机制异常时避免无限空转（如 API 每页都“ok”
                # 但服务端已无更多行），120 秒后强制结束，任务照常完成
                if time.time() - scan_started > 120:
                    log.warning("历史页扫描超时护栏(120s)：已取 %d/%d 条，提前结束",
                                len(items), hint_entries or 0)
                    break
                absorb(await page.evaluate(HISTORY_EXTRACT_JS))
                # 页面声明了下载总条数时，实时上报百分比（前端历史页阶段显示进度）
                if hint_entries and hint_entries > 0:
                    await self.progress.set_progress(
                        min(len(items), hint_entries), hint_entries)
                if self.max_history is not None and sorted_by_update and len(items) >= self.max_history:
                    break  # 达到按更新时间排序的条数上限即停；None = 抓全部（靠 hint/翻页结束）
                if hint_entries and hint_entries > 0 \
                        and len(items) >= hint_entries:
                    break  # 已经拿全页面声明的下载条数
                before = len(items)
                # DataTables 历史表：尝试用官方 API 翻下一页（最稳），
                # 失败则点 .paginate_button 的 Next/» 控件
                next_ok = False
                try:
                    r = await page.evaluate(
                        "() => { try { const t = $('#DataTables_Table_0')"
                        ".DataTable(); if (t.page.len() < 100) { "
                        "t.page.len(100).draw(); } "
                        "t.page('next').draw(false); "
                        "return 'ok'; } catch (e) { return 'err:' + e.message; } }"
                    )
                    next_ok = str(r).startswith("ok")
                except Exception:
                    pass
                if not next_ok:
                    try:
                        for btn in await page.query_selector_all(
                                "[class*='paginate']"):
                            text = (await btn.inner_text() or "").strip()
                            if re.search(r"next|»|›|>", text, re.I):
                                await btn.click(timeout=2000)
                                next_ok = True
                                break
                    except Exception:
                        pass
                if next_ok:
                    # 等待新页数据真正渲染（AJAX 未返回就再翻页会互相打断）
                    grew = False
                    for _w in range(2):   # AJAX 渲染等待：2 轮足够，避免空翻页时单轮 9.6s 的“卡住”感
                        await page.wait_for_timeout(1200)
                        if absorb(await page.evaluate(HISTORY_EXTRACT_JS)) > 0:
                            grew = True
                            break
                    if not grew:
                        stale += 1
                        empty_pages += 1
                        if empty_pages >= 2:
                            log.info("连续 %d 页无新条目：历史页已到末尾（已取 %d 条），提前收尾",
                                     empty_pages, len(items))
                            break
                    else:
                        empty_pages = 0
                    continue
                # 兼容非 DataTables 结构：旧式“加载更多”按钮
                clicked = False
                try:
                    buttons = await page.query_selector_all(
                        "button, a.btn, a[role=button], [class*='load-more'], "
                        "[class*='loadMore']")
                except Exception as exc:
                    log.warning("broad load-more selector failed: %s",
                                _exc_brief(exc))
                    buttons = []
                for btn in buttons:
                    try:
                        text = (await btn.inner_text() or "").strip()
                    except Exception:
                        continue
                    if text and len(text) < 60 and re.search(
                            r"load more|show more|view more|加载更多|下一页",
                            text, re.I):
                        try:
                            await btn.click(timeout=2500)
                            clicked = True
                            await page.wait_for_timeout(1600)
                            break
                        except Exception:
                            pass
                if clicked:
                    continue
                if len(items) == before:
                    stale += 1
                if stale >= 3:
                    log.info("历史页分页连续 %d 轮无新条目，提前结束（已取 %d 条，声明 %s 条）",
                             stale, len(items), hint_entries or "-")
                    break
        finally:
            # 现场取证：把历史页 DOM 与"加载控件"清单存盘供排查分页机制
            try:
                html_dump = await page.content()
                (config.CACHE_DIR / "history-debug.html").write_text(
                    html_dump, encoding="utf-8", errors="replace")
                controls = await page.evaluate(r"""
                () => {
                  const out = [];
                  document.querySelectorAll('*').forEach((el) => {
                    if (el.children.length > 4) return;
                    let t = '';
                    try { t = (el.innerText || '').trim(); } catch (e) {}
                    if (t && t.length < 40 && /load|more|next|older|page|下一页|加载|view/i.test(t)) {
                      let cls = '';
                      try { cls = String(el.className || '').slice(0, 60); } catch (e) {}
                      out.push(el.tagName + '.' + cls + ' :: ' + t);
                    }
                  });
                  return [...new Set(out)].slice(0, 80);
                }
                """)
                (config.CACHE_DIR / "history-controls.txt").write_text(
                    "\n".join(controls), encoding="utf-8")
                log.info("history debug dumped (%d controls)",
                         len(controls or []))
            except Exception as exc:
                log.warning("history debug dump failed: %s", _exc_brief(exc))
            # merge xhr-found entries (keeps items we couldn't see in DOM)
            for rec in xhr_found:
                if rec["domain"] != config.TARGET_GAME:
                    continue  # 只读取指定游戏的模组
                key = f"{rec['domain']}/{rec['mod_id']}"
                if key not in seen:
                    seen[key] = rec
            items[:] = list(seen.values())
            await page.close()

        if not items:
            raise RuntimeError(
                f"下载历史中没有找到游戏「{config.TARGET_GAME}」的模组："
                "可能登录态已失效、该游戏没有下载记录，或页面结构发生变化。"
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
