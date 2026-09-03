"""Offline self-check for the nexus-dashboard (no network, no browser launch).

    python self_check.py
exits non-zero on any failure.
"""

from __future__ import annotations

import json
import pathlib
import py_compile
import sys
from datetime import datetime

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
        assert r.status_code == 200 and "Nexus Mods" in r.text, r.status_code
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
    check("py files compile", test_compile)
    check("nexus date parser", test_nexus_date)
    check("mod meta parser", test_mod_meta)
    check("files section parser", test_files_html)
    check("files structured parser (Nexus 2.0)", test_files_structured)
    check("changelog parser", test_changelog_text)
    check("changelog structured parser", test_changelog_html)
    check("download-window filter (update>=download)", test_filter_downloads)
    check("light filter skips crawling", test_light_filter)
    check("progress stage flips running", test_progress_stage_marks_running)
    check("demo data build + schema", test_demo_data)
    check("discard-tag API (post-processing)", test_discard_api)
    check("FastAPI smoke (/ /api/status /api/mods /api/export)", test_api_smoke)

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()