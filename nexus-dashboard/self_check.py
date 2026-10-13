"""Offline self-check for the nexus-dashboard (no network, no browser launch).

    python self_check.py
exits non-zero on any failure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import py_compile
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bs4 import BeautifulSoup

import config
import make_demo
from scraper import (
    parse_changelog_text,
    parse_files_html,
    parse_mod_meta,
    parse_nexus_date,
)

FAILURES: list[str] = []


def check(name: str, fn):
    try:
        fn()
        print(f"  PASS  {name}")
    except Exception as exc:
        FAILURES.append(f"{name}: {exc}")
        print(f"  FAIL  {name}: {exc}")


def test_compile() -> None:
    root = pathlib.Path(__file__).resolve().parent
    for f in ("app.py", "scraper.py", "session_manager.py", "config.py",
              "make_demo.py", "self_check.py"):
        py_compile.compile(str(root / f), doraise=True)


def test_nexus_date() -> None:
    d = parse_nexus_date("03 May 2025, 8:07PM")
    assert d is not None and d.year == 2025 and d.month == 5 and d.day == 3, d
    d2 = parse_nexus_date("15 Jan 2023")
    assert d2 is not None and d2.year == 2023 and d2.month == 1, d2
    # Nexus 2.0 pages use numeric dates: "Last updated on 2026/8/27 by ..."
    d3 = parse_nexus_date("on 2026/8/27 by <a href='x'>Author</a>")
    assert d3 is not None and d3.year == 2026 and d3.month == 8 and d3.day == 27, d3
    d4 = parse_nexus_date("05-Aug-2026")
    assert d4 is not None and d4.year == 2026 and d4.month == 8, d4
    for text in ("2026-04-01 16:55", "2026/4/1, 16:55",
                 "01 April 2026, 16:55", "01 April 2026, 4:55 pm"):
        assert parse_nexus_date(text) == datetime(2026, 4, 1, 16, 55), text
    for text in ("2026-04-01T16:55:00+08:00", "01 April 2026, 16:55 +0800"):
        d = parse_nexus_date(text)
        assert d is not None and d.astimezone(timezone.utc) == datetime(
            2026, 4, 1, 8, 55, tzinfo=timezone.utc), (text, d)
    assert parse_nexus_date("01 April 2026, 16:55 UTC") == datetime(
        2026, 4, 1, 16, 55, tzinfo=timezone.utc)
    assert parse_nexus_date("not a date") is None


def test_mod_meta() -> None:
    html = """<html><head>
      <meta property="og:title" content="SkyUI - 测试模组" />
      <meta name="description" content="一个测试简介" />
    </head><body><h1>SkyUI <span>SE</span></h1>
      <div>Author: Sus620
      Last updated: 03 May 2025, 8:07PM
      Created: 01 Jan 2024, 3:00PM
      Category: User Interface</div></body></html>"""
    soup = BeautifulSoup(html, "html.parser")
    meta = parse_mod_meta(soup, soup.get_text("\n"))
    assert meta["name"].startswith("SkyUI"), meta
    assert meta["author"] == "Sus620", meta
    assert meta["updated_at"] and meta["updated_at"].startswith("2025"), meta
    assert meta["category"] == "User Interface", meta
    # The metadata fallback must not reduce an accurate timestamp to midnight.
    with patch("scraper.TZ_OFFSET_MIN", -480):
        meta = parse_mod_meta(soup, soup.get_text("\n"))
    assert meta["updated_at"] == "2025-05-03T12:07:00+00:00", meta


def test_files_html() -> None:
    html = """<div class="files">
      <h3>Main files</h3>
      <table><tbody>
        <tr><td><a href="?file_id=201" class="file-name">SkyUI_5.2.7z</a></td>
            <td>5.2</td><td>11.5 MB</td><td>01 Feb 2025, 10:00AM</td></tr>
      </tbody></table>
      <h3>Optional files</h3>
      <table><tbody>
        <tr><td><a href="?file_id=202">Fonts_CN.zip</a></td>
            <td>1.0</td><td>2.1 MB</td><td>10 Mar 2025, 9:00AM</td></tr>
      </tbody></table>
      <h3>Old files</h3>
      <table><tbody><tr><td><a href="?file_id=203">SkyUI_5.1.7z</a></td>
        <td>5.1</td><td>11.4 MB</td><td>01 Dec 2024, 2:00PM</td></tr></tbody></table>
    </div>"""
    out = parse_files_html(BeautifulSoup(html, "html.parser"), BeautifulSoup(html, "html.parser").get_text("\n"))
    assert len(out["Main files"]) == 1, out
    assert out["Main files"][0]["name"] == "SkyUI_5.2.7z", out
    assert out["Main files"][0]["file_id"] == "201", out
    assert out["Main files"][0]["size"] == "11.5 MB", out
    assert out["Main files"][0]["version"] == "5.2", out
    assert len(out["Optional files"]) == 1, out
    assert len(out["Old files"]) == 1, out


def test_changelog_text() -> None:
    text = """Version 5.2
    03 May 2025, 8:07PM
    修复物品栏卡顿\n- 更新脚本
    Version 5.1
    01 Feb 2025, 10:00AM
    新增搜索高亮"""
    entries = parse_changelog_text(text)
    assert len(entries) >= 2, entries
    assert entries[0]["version"] == "5.2", entries
    assert entries[0]["date"], entries
    assert entries[0]["text"], entries
    assert "更新脚本" in entries[0]["text"], entries
    # single-line header style: "ModName X.Y  -  01 Jan 2025, 1:00PM"
    text2 = "SkyUI 5.0 - 01 Jan 2025, 1:00PM\nnew feature line"
    single = parse_changelog_text(text2)
    assert len(single) == 1, single
    assert single[0]["version"] == "5.0" and single[0]["date"], single
    assert "new feature line" in single[0]["text"], single


def test_filter_downloads() -> None:
    from scraper import filter_old_downloads

    def mk(dl, up):
        return {"downloaded_at": dl, "updated_at": up}

    kept, removed = filter_old_downloads([
        mk("2025-03-01T00:00:00+00:00", "2025-03-10T00:00:00+00:00"),  # 更新≥下载 → 保留
        mk("2025-03-10T00:00:00+00:00", "2025-03-01T00:00:00+00:00"),  # 下载晚于更新 → 去除
        mk(None, "2025-03-01T00:00:00+00:00"),                          # 无下载时间 → 保留
        mk("2025-03-01T00:00:00+00:00", "2025-03-01T00:00:00+00:00"),  # 相等 → 保留
    ])
    assert removed == 1, (kept, removed)
    assert len(kept) == 3, kept
    assert "2025-03-10" in kept[0]["updated_at"]
    # Compare instants, not ISO strings: local 14:02 is earlier than UTC 08:55.
    before = mk("2026-04-01T14:02:00+08:00", "2026-04-01T08:55:00Z")
    after = mk("2026-04-01T08:55:00Z", "2026-04-01T14:02:00+08:00")
    kept, removed = filter_old_downloads([before, after])
    assert kept == [before] and removed == 1, (kept, removed)


def test_demo_data() -> None:
    make_demo.main()
    data = json.loads(config.DEMO_DATA_FILE.read_text(encoding="utf-8"))
    assert len(data) >= 5
    for m in data:
        for key in ("mod_id", "game_domain", "name", "updated_at", "files",
                    "changelog", "url"):
            assert key in m, (key, m.get("name"))
        for section in config.FILE_SECTIONS:
            assert section in m["files"], (section, m["name"])
    # sort keys must be monotone non-increasing (descending by updated_at)
    keys = [m.get("updated_at") or m.get("created_at") or "0000" for m in data]
    assert keys == sorted(keys, reverse=True), "demo data not sorted desc"
    assert "5.2" in data[0]["changelog"][0]["version"]


def test_progress_stage_marks_running() -> None:
    import asyncio

    from scraper import ProgressTracker

    pt = ProgressTracker()

    async def go():
        assert pt.snapshot()["running"] is False
        await pt.stage("collecting_history")
        return pt.snapshot()

    snap = asyncio.run(go())
    assert snap["running"] is True, snap
    assert snap["stage"] == "collecting_history", snap
    assert snap["started_at"], snap


def test_light_filter() -> None:
    from scraper import light_filter_items

    def mk(dl, up):
        return {"cells": ["name", dl, "auth", "cat", up]}

    kept, removed = light_filter_items([
        mk("1 September 2026, 8:06 pm", "30 August 2026, 12:00 pm"),  # 下载晚于更新 → 不爬
        mk("27 August 2026, 8:06 pm", "30 August 2026, 12:00 pm"),    # 更新≥下载 → 爬
        mk("", "30 August 2026, 12:00 pm"),                            # 缺下载时间 → 保守爬
        {},                                                            # 无 cells → 保守爬
    ])
    assert removed == 1, (kept, removed)
    assert len(kept) == 3, kept


def _history_item(dl: str, up: str, mod_id: int = 42) -> dict:
    return {"domain": config.TARGET_GAME, "mod_id": mod_id,
            "cells": ["Example mod", dl, "author", "category", up]}


def test_same_day_download_regression() -> None:
    from scraper import NexusScraper, _history_date_tokens, light_filter_items

    downloaded = _history_item("2026-04-01 16:55", "2026-04-01 14:02")
    downloaded["_earliest_dl"] = "2026-03-01T00:00:00+00:00"
    pending = _history_item("2026-04-01 13:55", "2026-04-01 14:02", 43)
    named = _history_item("01 April 2026, 16:55", "01 April 2026, 14:02", 44)
    unknown = _history_item("", "2026-04-01 14:02", 45)
    # A generic row may contain the update date; it is not a missing Last DL.
    unknown["rowText"] = "Example mod 2026-04-01 14:02"
    assert _history_date_tokens(unknown) == ("", "2026-04-01 14:02")
    with patch("scraper.TZ_OFFSET_MIN", -480):
        kept, removed = light_filter_items([downloaded, pending, named, unknown])
        displayed = NexusScraper(None)._slim_entry(downloaded)
        unknown_display = NexusScraper(None)._slim_entry(unknown)
    assert kept == [pending, unknown] and removed == 2, (kept, removed)
    assert displayed["updated_at"] == "2026-04-01T06:02:00+00:00", displayed
    assert displayed["downloaded_at"] == "2026-04-01T08:55:00+00:00", displayed
    assert unknown_display["downloaded_at"] is None, unknown_display


def test_history_merge_latest_download() -> None:
    from scraper import NexusScraper, _merge_history_item, light_filter_items

    older = _history_item("2026-03-01 12:00", "2026-04-01 14:02")
    latest = _history_item("2026-04-01 16:55", "2026-04-01 14:02")
    middle = _history_item("2026-04-01 13:55", "2026-04-01 14:02")
    unknown = _history_item("", "2026-04-01 14:02")
    # Merge is shared by same-page and later-page rows; neither encounter
    # order nor a missing date may replace the most recent known download.
    for rows in ([older, middle, latest, unknown],
                 [latest, unknown, middle, older]):
        seen = {}
        added = [_merge_history_item(seen, row) for row in rows]
        assert added == [True, False, False, False], added
        assert list(seen.values()) == [latest], seen
        assert light_filter_items(list(seen.values())) == ([], 1)
        displayed = NexusScraper(None)._slim_entry(next(iter(seen.values())))
        assert parse_nexus_date(displayed["downloaded_at"]).hour == 16, displayed
    # Prefer the richer DOM row when it describes the same instant as XHR.
    xhr = {"domain": config.TARGET_GAME, "mod_id": 42,
           "rowText": "2026-04-01 16:55"}
    seen = {}
    _merge_history_item(seen, xhr)
    _merge_history_item(seen, latest)
    assert list(seen.values()) == [latest], seen


def test_scan_cache_timezone() -> None:
    import scraper

    item = _history_item("2026-04-01 16:55", "2026-04-01 14:02")
    sc = scraper.NexusScraper(None)
    with patch.object(scraper, "TZ_OFFSET_MIN", -480):
        sc._save_scan_cache([item])
        saved = json.loads(config.SCAN_CACHE_FILE.read_text(encoding="utf-8"))
        assert saved["timezone_offset_min"] == -480, saved
        expected = sc._slim_entry(item)
        scraper.TZ_OFFSET_MIN = 0  # Simulate a fresh backend process.
        restored = sc._load_scan_cache()
        assert restored == [item], restored
        assert scraper.TZ_OFFSET_MIN == -480, scraper.TZ_OFFSET_MIN
        assert sc._slim_entry(restored[0]) == expected
        assert scraper.light_filter_items(restored) == ([], 1)
        saved.pop("timezone_offset_min")
        config.SCAN_CACHE_FILE.write_text(json.dumps(saved), encoding="utf-8")
        assert sc._load_scan_cache() is None, "cache without timezone must be rescanned"


def test_refresh_empty_success() -> None:
    import app as app_module

    state = app_module.AppState()
    state.mods = [{"mod_id": 42}]
    state.refresh_version = 7
    state.refresh_error = "previous error"
    sc = SimpleNamespace(run=AsyncMock(return_value=[]), filter_removed=1)
    playwright = MagicMock()
    with patch.object(app_module, "state", state), \
            patch.object(app_module, "runner", return_value=sc), \
            patch.object(app_module, "async_playwright", return_value=playwright):
        asyncio.run(app_module.refresh_job(force=True))
    assert state.mods == [], state.mods
    assert state.refresh_version == 8, state.refresh_version
    assert state.last_refresh and state.refresh_error is None
    assert state.filter_stats == {
        "game": config.TARGET_GAME, "kept": 0, "removed_old_downloads": 1,
    }, state.filter_stats
    assert state.progress.snapshot()["running"] is False
    sc.run.assert_awaited_once_with(playwright.__aenter__.return_value, force_refresh=True)


def test_refresh_failure_preserves_results() -> None:
    import app as app_module

    state = app_module.AppState()
    previous = [{"mod_id": 42}]
    state.mods = previous
    state.refresh_version = 7
    state.last_refresh = "previous scan"
    state.filter_stats = {"kept": 1}
    sc = SimpleNamespace(run=AsyncMock(side_effect=RuntimeError("test scan failure")))
    with patch.object(app_module, "state", state), \
            patch.object(app_module, "runner", return_value=sc), \
            patch.object(app_module, "async_playwright", return_value=MagicMock()), \
            patch.object(app_module.log, "exception"):
        asyncio.run(app_module.refresh_job(force=True))
    assert state.mods is previous, state.mods
    assert state.refresh_version == 7 and state.last_refresh == "previous scan"
    assert state.filter_stats == {"kept": 1}, state.filter_stats
    assert state.refresh_error == "test scan failure", state.refresh_error
    assert state.progress.snapshot()["running"] is False


def test_files_structured() -> None:
    from scraper import parse_files_html

    html = """<div class="file-container-main-files"><div class="file-category-header"><h2>Main files</h2></div>
    <dl class="accordion"><dt class="file-expander-header" data-id="797330"
      data-name="SkyUI_5.2.7z" data-size="11776" data-version="5.2"
      data-date="1750000000"></dt></dl></div>
    <div class="file-category-header"><h2>Optional files</h2></div>
    <dl><dt class="file-expander-header" data-name="Fonts_CN.zip"
      data-size="2048" data-version="1.0" data-date="1749000000"></dt></dl>"""
    out = parse_files_html(BeautifulSoup(html, "html.parser"), html)
    assert len(out["Main files"]) == 1, out["Main files"]
    m = out["Main files"][0]
    assert m["name"] == "SkyUI_5.2.7z" and m["version"] == "5.2", m
    assert m["size"] == "11.5 MB", m          # 11776 KB -> 11.5 MB
    assert m["uploaded_at"], m                # epoch -> ISO
    assert len(out["Optional files"]) == 1, out["Optional files"]


def test_changelog_html() -> None:
    from scraper import parse_changelog_html

    html = """<ul class="change-logs">
      <li><h3>Version 4.6</h3><div class="log-change"><ul class="arrowlist">
        <li>Updated for SKSE64 AE/SE 2.2.6 &amp; Skyrim 1.6.1170</li>
        <li>Fixed a crash</li></ul></div></li>
      <li><h3>Version 4.5</h3><div class="log-change"><ul class="arrowlist">
        <li>Updated for SKSE64 AE/SE 2.2.4</li></ul></div></li>
    </ul>"""
    entries = parse_changelog_html(BeautifulSoup(html, "html.parser"))
    assert len(entries) == 2, entries
    assert entries[0]["version"] == "4.6", entries
    assert "SKSE64" in entries[0]["text"], entries
    assert "crash" in entries[0]["text"], entries
    assert entries[1]["version"] == "4.5", entries


def test_discard_api() -> None:
    from fastapi.testclient import TestClient
    import app as app_module

    with TestClient(app_module.app) as client:
        client.post("/api/discarded/toggle", json={"mod_id": 12345})
        got = client.get("/api/discarded").json()
        assert 12345 in got["ids"], got
        client.post("/api/discarded/toggle", json={"mod_id": 12345})
        got = client.get("/api/discarded").json()
        assert 12345 not in got["ids"], got
    if app_module.config.DISCARD_FILE.exists():
        app_module.config.DISCARD_FILE.unlink()


def test_api_smoke() -> None:
    from fastapi.testclient import TestClient
    import app as app_module

    app_module.load_demo()
    with TestClient(app_module.app) as client:
        r = client.get("/")
        assert r.status_code == 200, r.status_code
        assert 'id="modList"' in r.text and 'id="settingsDialog"' in r.text, "dashboard markup missing"
        s = client.get("/api/status").json()
        assert s["demo"] is True and s["mods_count"] > 0, s
        m = client.get("/api/mods").json()
        assert m["total"] > 0 and len(m["items"]) == m["total"], m
        items = m["items"]
        keys = [x.get("updated_at") or x.get("created_at") or "0000" for x in items]
        assert keys == sorted(keys, reverse=True), "api not sorted desc"
        e = client.get("/api/export")
        assert e.status_code == 200 and len(e.json()) > 0


def main() -> None:
    print("nexus-dashboard self-check")
    FAILURES.clear()
    # Importing app initializes its session manager and log file, so redirect
    # every runtime-data path before the first app import. User data is never
    # read, overwritten, or deleted by the self-check suite.
    with tempfile.TemporaryDirectory(prefix="nexus-self-check-") as temp_dir:
        root = pathlib.Path(temp_dir)
        cache = root / "cache" / "nexus-dashboard"
        paths = {
            "REPO_ROOT": root, "CACHE_DIR": cache,
            "SESSION_FILE": cache / "session.json",
            "SESSION_META_FILE": cache / "session.meta.json",
            "DEMO_DATA_FILE": cache / "demo_mods.json",
            "MODS_CACHE_DIR": cache / "mods",
            "SCAN_CACHE_FILE": cache / "history-scan.json",
            "DISCARD_FILE": cache / "discarded.json",
            "PIP_CACHE_DIR": cache / "pip",
        }
        old_handlers = set(logging.getLogger().handlers)
        with patch.multiple(config, **paths), patch("scraper.TZ_OFFSET_MIN", 0):
            config.ensure_dirs()
            try:
                check("py files compile", test_compile)
                check("nexus date parser", test_nexus_date)
                check("mod meta parser", test_mod_meta)
                check("files section parser", test_files_html)
                check("files structured parser (Nexus 2.0)", test_files_structured)
                check("changelog parser", test_changelog_text)
                check("changelog structured parser", test_changelog_html)
                check("download-window filter (update>=download)", test_filter_downloads)
                check("light filter skips crawling", test_light_filter)
                check("same-day latest download regression", test_same_day_download_regression)
                check("history merge keeps latest download", test_history_merge_latest_download)
                check("scan cache preserves timezone", test_scan_cache_timezone)
                check("empty successful refresh replaces old results", test_refresh_empty_success)
                check("failed refresh preserves old results", test_refresh_failure_preserves_results)
                check("progress stage flips running", test_progress_stage_marks_running)
                check("demo data build + schema", test_demo_data)
                check("discard-tag API (post-processing)", test_discard_api)
                check("FastAPI smoke (/ /api/status /api/mods /api/export)", test_api_smoke)
            finally:
                # Windows cannot delete the temporary app.log while open.
                for handler in set(logging.getLogger().handlers) - old_handlers:
                    logging.getLogger().removeHandler(handler)
                    handler.close()

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
