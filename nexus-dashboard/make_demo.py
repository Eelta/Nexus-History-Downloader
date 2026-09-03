"""Generate bundled demo data (no login / no network needed) for the dashboard.

    python make_demo.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import config


def iso(days_ago: int, hour: int = 12) -> str:
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return t.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def main() -> None:
    config.ensure_dirs()
    demo = [
        {
            "mod_id": 1001,
            "game_domain": "skyrimspecialedition",
            "url": "https://www.nexusmods.com/skyrimspecialedition/mods/1001",
            "name": "实例模组 SkyUI SE（演示数据）",
            "author": "Sus620",
            "summary": "用于演示仪表盘界面的示例模组：Skyrim 界面增强。",
            "category": "User Interface",
            "updated_at": iso(0),
            "created_at": iso(400),
            "downloaded_at": iso(2),
            "files": {
                "Main files": [
                    {"name": "SkyUI_5.2_SE.7z", "version": "5.2", "size": "11.5 MB",
                     "uploaded_at": iso(30), "uploaded_raw": "", "file_id": "1000001"},
                ],
                "Optional files": [
                    {"name": "SkyUI_English_fonts_optional.zip", "version": "1.0",
                     "size": "2.1 MB", "uploaded_at": iso(60), "uploaded_raw": "",
                     "file_id": "1000002"},
                    {"name": "SkyUI_SE_Chinese_SSE_patch.7z", "version": "2.1",
                     "size": "890 KB", "uploaded_at": iso(55), "uploaded_raw": "",
                     "file_id": "1000003"},
                ],
                "Old files": [
                    {"name": "SkyUI_5.1_SE.7z", "version": "5.1", "size": "11.4 MB",
                     "uploaded_at": iso(120), "uploaded_raw": "", "file_id": "1000004"},
                ],
                "Miscellaneous": [],
            },
            "changelog": [
                {"version": "5.2", "date": iso(30), "label": "5.2",
                 "text": "修复了物品栏排序卡顿问题；适配最新游戏版本。\n- 修复快速存取列表刷新\n- 更新脚本版本"},
                {"version": "5.1", "date": iso(120), "label": "5.1",
                 "text": "新增箱内搜索高亮；修复若干本地化问题。"},
            ],
            "source": "demo",
        },
        {
            "mod_id": 1002,
            "game_domain": "skyrimspecialedition",
            "url": "https://www.nexusmods.com/skyrimspecialedition/mods/1002",
            "name": "实例模组 3D 装备细节（演示数据）",
            "author": "DemoAuthor",
            "summary": "更精细的装备模型替换，示例数据用于展示文件分组。",
            "category": "Models and Textures",
            "updated_at": iso(1, hour=20),
            "created_at": iso(300),
            "downloaded_at": iso(5),
            "files": {
                "Main files": [
                    {"name": "3DArmor_Full_v2.2.zip", "version": "2.2", "size": "842 MB",
                     "uploaded_at": iso(1), "uploaded_raw": "", "file_id": "1000011"},
                ],
                "Optional files": [
                    {"name": "3DArmor_4K_textures.7z", "version": "2.2", "size": "1.2 GB",
                     "uploaded_at": iso(1), "uploaded_raw": "", "file_id": "1000012"},
                    {"name": "3DArmor_1K_lowres.zip", "version": "2.0", "size": "210 MB",
                     "uploaded_at": iso(90), "uploaded_raw": "", "file_id": "1000013"},
                ],
                "Old files": [{"name": "3DArmor_Full_v2.0.zip", "version": "2.0",
                               "size": "780 MB", "uploaded_at": iso(90), "uploaded_raw": "",
                               "file_id": "1000014"}],
                "Miscellaneous": [{"name": "FAQs.txt", "version": "", "size": "4 KB",
                                   "uploaded_at": iso(300), "uploaded_raw": "",
                                   "file_id": "1000015"}],
            },
            "changelog": [
                {"version": "2.2", "date": iso(1), "label": "2.2",
                 "text": "重做肩甲 UV，修复缝隙；补充 4K 材质。"},
            ],
            "source": "demo",
        },
        {
            "mod_id": 1003,
            "game_domain": "stardewvalley",
            "url": "https://www.nexusmods.com/stardewvalley/mods/1003",
            "name": "实例模组 自动采集（演示数据）",
            "author": "ForgeUser",
            "summary": "自动收获作物与果树，示例数据。",
            "category": "Gameplay",
            "updated_at": iso(2),
            "created_at": iso(150),
            "downloaded_at": iso(8),
            "files": {
                "Main files": [{"name": "AutoHarvest_1.4.2.dll", "version": "1.4.2",
                                "size": "96 KB", "uploaded_at": iso(2), "uploaded_raw": "",
                                "file_id": "1000021"}],
                "Optional files": [{"name": "AutoHarvest_Chinese.json", "version": "1.4",
                                    "size": "3 KB", "uploaded_at": iso(60), "uploaded_raw": "",
                                    "file_id": "1000022"}],
                "Old files": [{"name": "AutoHarvest_1.3.0.dll", "version": "1.3.0",
                               "size": "90 KB", "uploaded_at": iso(80), "uploaded_raw": "",
                               "file_id": "1000023"}],
                "Miscellaneous": [],
            },
            "changelog": [
                {"version": "1.4.2", "date": iso(2), "label": "1.4.2",
                 "text": "兼容 SMAPI 4.x；修复温室作物漏收。"},
            ],
            "source": "demo",
        },
        {
            "mod_id": 1004,
            "game_domain": "cyberpunk2077",
            "url": "https://www.nexusmods.com/cyberpunk2077/mods/1004",
            "name": "实例模组 夜城光影（演示数据）",
            "author": "NightCityLG",
            "summary": "光线重构与体积雾调整，示例数据。",
            "category": "Visuals",
            "updated_at": iso(3),
            "created_at": iso(100),
            "downloaded_at": iso(10),
            "files": {
                "Main files": [{"name": "NCLighting_v3.0.zip", "version": "3.0",
                                "size": "320 MB", "uploaded_at": iso(3), "uploaded_raw": "",
                                "file_id": "1000031"}],
                "Optional files": [{"name": "NCLighting_NoFog.ini", "version": "3.0",
                                    "size": "1 KB", "uploaded_at": iso(3), "uploaded_raw": "",
                                    "file_id": "1000032"}],
                "Old files": [], "Miscellaneous": [],
            },
            "changelog": [
                {"version": "3.0", "date": iso(3), "label": "3.0",
                 "text": "全新体积雾参数，帧数影响降低 15%。"},
            ],
            "source": "demo",
        },
        {
            "mod_id": 1005,
            "game_domain": "baldursgate3",
            "url": "https://www.nexusmods.com/baldursgate3/mods/1005",
            "name": "实例模组 背包整理（演示数据）",
            "author": "BagMaster",
            "summary": "一键整理与分类背包物品，示例数据。",
            "category": "User Interface",
            "updated_at": iso(5),
            "created_at": iso(80),
            "downloaded_at": iso(12),
            "files": {
                "Main files": [{"name": "BagSort_v1.9.1.pak", "version": "1.9.1",
                                "size": "148 KB", "uploaded_at": iso(5), "uploaded_raw": "",
                                "file_id": "1000041"}],
                "Optional files": [{"name": "BagSort_NoDurability.pak", "version": "1.9",
                                    "size": "146 KB", "uploaded_at": iso(20), "uploaded_raw": "",
                                    "file_id": "1000042"}],
                "Old files": [{"name": "BagSort_v1.8.pak", "version": "1.8",
                               "size": "140 KB", "uploaded_at": iso(60), "uploaded_raw": "",
                               "file_id": "1000043"}],
                "Miscellaneous": [],
            },
            "changelog": [
                {"version": "1.9.1", "date": iso(5), "label": "1.9.1",
                 "text": "修复与官方补丁 5 的兼容性。"},
            ],
            "source": "demo",
        },
        {
            "mod_id": 1006,
            "game_domain": "witcher3",
            "url": "https://www.nexusmods.com/witcher3/mods/1006",
            "name": "实例模组 高清马匹（演示数据）",
            "author": "StableHand",
            "summary": "高清马匹模型与纹理，示例数据。",
            "category": "Models and Textures",
            "updated_at": iso(9),
            "created_at": iso(200),
            "downloaded_at": iso(15),
            "files": {
                "Main files": [{"name": "HDHorses_v2.1.zip", "version": "2.1",
                                "size": "450 MB", "uploaded_at": iso(9), "uploaded_raw": "",
                                "file_id": "1000051"}],
                "Optional files": [], "Old files": [], "Miscellaneous": [],
            },
            "changelog": [],
            "source": "demo",
        },
        {
            "mod_id": 1007,
            "game_domain": "cyberpunk2077",
            "url": "https://www.nexusmods.com/cyberpunk2077/mods/1007",
            "name": "实例模组 义体扩展（演示数据）",
            "author": "RipperDoc",
            "summary": "新增多种义体与数值效果，示例数据。",
            "category": "Gameplay",
            "updated_at": iso(12),
            "created_at": iso(60),
            "downloaded_at": iso(20),
            "files": {
                "Main files": [{"name": "CyberwarePlus_v0.9.3.zip", "version": "0.9.3",
                                "size": "58 MB", "uploaded_at": iso(12), "uploaded_raw": "",
                                "file_id": "1000061"}],
                "Optional files": [{"name": "CyberwarePlus_Balanced.ini", "version": "0.9",
                                    "size": "2 KB", "uploaded_at": iso(30), "uploaded_raw": "",
                                    "file_id": "1000062"}],
                "Old files": [], "Miscellaneous": [],
            },
            "changelog": [
                {"version": "0.9.3", "date": iso(12), "label": "0.9.3",
                 "text": "修复斯安威斯坦冷却叠加 bug。"},
            ],
            "source": "demo",
        },
    ]
    config.DEMO_DATA_FILE.write_text(
        json.dumps(demo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {len(demo)} demo mods -> {config.DEMO_DATA_FILE}")


if __name__ == "__main__":
    main()