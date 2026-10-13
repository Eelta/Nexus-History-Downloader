# Nexus History Downloader

[English](README.md) | **简体中文**

## 功能

- **独立桌面界面**：无需浏览器，应用自带窗口加载本地仪表盘；
- **更新监测**：一次登录扫描 N 网全部下载历史，对比更新时间与最近一次下载时间，筛除更新后已下载的模组；旧时区格式的缓存会自动失效并重新扫描，列表一次全量加载（无分页）；
- **多线程下载**：等效 PCL 引擎，HTTP Range 分段并行（默认 256 连接，直接使用滑块上限），源站不支持 Range 时自动降级单线程；
- **下载接管**：点击模组名打开受控浏览器窗口（使用真实登录态），窗口内所有 N 网下载自动转交引擎；
- **通过 MO2 安装**：下载后自动进入持久化安装队列，玩家确认或修改与已安装模组匹配的名称后，使用 MO2 原生安装流程，保留 FOMOD 选项及合并 / 替换提示；可选仅在成功后删除安装包；
- **零维护**：缓存自动清理（残留分片、pip 缓存、日志限长）。

## 环境要求

- Windows 10/11（含 WebView2 运行时，Edge 自带）；
- 已安装 Microsoft Edge 或 Google Chrome（二选一，**不会自动下载浏览器**；两者皆无则无法登录/接管）。

## 构建与发布

```cmd
build-exe.cmd
```

- 一键产出 `dist\NHD.exe`（内含应用本体）；

## 安装与卸载

1. 运行 `NHD.exe`：选择安装文件夹，可选创建桌面快捷方式；
2. 安装完成后，从该文件夹运行 `NHD.exe`（应用与缓存数据同在该文件夹）；
3. 已注册为 Windows 应用：可在「设置 → 应用」中卸载（删除安装文件夹全部内容，卸载前请确认）。

卸载程序使用 Windows 自带的 .NET Framework 和 PowerShell，单独体积仅数十 KB，不再复制整个安装包。

## 首次使用

1. 打开应用后在页面点「浏览器登录」，用 Edge/Chrome 真实资料登录一次（仅保存 N 网会话，不存密码）；
2. 列表中的模组即「已下载且有更新」的条目；点击模组名 → 接管窗口直达文件页 → 点任意下载自动转交引擎。

## 下载后安装到 MO2

1. 打开「设置 → MO2」，选择包含 `ModOrganizer.exe` 的目录，默认为 `F:\Skyrim\iniRePather-Skyrim`，可自行修改。选择后会自动保存并配置随附的连接插件，首次使用请打开或重启 MO2。
2. 保持「下载后安装到 MO2」启用。下载器会综合 Nexus ID、名称，以及可读取的安装包文件列表与已安装模组文件路径，给出已有模组名称的候选建议。每个安装包都必须由玩家确认名称；可以选择其他候选，也可以直接改成自己想要的名称。
3. 确认名称后点击「安装」，随后在 MO2 窗口完成原生安装流程。FOMOD 步骤、可选组件，以及同名模组的合并 / 替换，均由玩家自己选择。
4. 可勾选「安装成功后删除安装包」。只有 MO2 明确返回成功后才删除该下载包；取消、失败或跳过都会保留。默认保留安装包。

已有归档模组资料和丢弃标记会保留；下载遇到同名文件时自动使用新的文件名。下载完成记录和安装队列在重启后仍然存在，安装模组不会清除其记录信息。

灰黑界面默认最大化，设置集中在右上角。支持即时搜索；`/` 聚焦搜索，`Ctrl+,` 打开设置。背景默认读取 `E:\BaiduSyncdisk\壁纸图片`，运行时每 5 分钟随机淡入下一张；在「设置 → 背景」调整目录、透明度或关闭背景（透明度越高，图片越淡）。

所有设置自动应用并保存，无需保存按钮。路径输入暂停后自动保存，开关即时生效；「设置 → 外观」可拖动调整字号（11–22），立即预览。字号及并发线程会在重启后保留，线程数对新下载任务生效。

## 源码运行（开发）

```powershell
powershell -ExecutionPolicy Bypass -File start.ps1   # 一键启动（构建引擎 + 仪表盘）
python nexus-dashboard\app.py --demo                 # 离线演示
python nexus-dashboard\self_check.py                 # 离线自检
```

## 目录结构

```
build-exe.cmd          一键构建安装包（引擎 + 后端 + 桌面壳 + 安装器合并）
src\Downloader.Core    下载引擎
src\Downloader.Host    下载宿主（HTTP 服务，被 dashboard 调用）
src\AppShell           桌面壳（WebView2 窗口，含后端提取）
src\Installer          安装器（GUI 向导 + 卸载注册）
src\Uninstaller        轻量卸载器（使用 Windows 自带运行时）
nexus-dashboard\       FastAPI 仪表盘（Python 后端）
tests\                 引擎自检
dist\                  编译产物（NHD.exe）
```

## 常用配置

- **下载目录**：在「设置 → 下载」修改，或编辑应用旁 `settings.json`（`{"DefaultDownloadDir":"D:\\Downloads"}`）；
- **并发线程**：在「设置 → 下载」实时调整（8~256，默认 256）；环境变量 `CUSTOMDL_MAX_THREADS` 可设初始值；
- **MO2 安装**：在「设置 → MO2」修改目录、自动安装及安装包清理选项，与现有下载设置一同保存；
- **并行任务**：环境变量 `CUSTOMDL_MAX_JOBS`（默认 5）；
- **宿主端口**：环境变量 `CUSTOMDL_HOST_PORT`（默认 18765）。

## 缓存与隐私

- 自动清理：残留下载分片（>24h）、pip 缓存、日志（限约 2000 行）；
- 下载完成历史（`cache/takeover-history.jsonl`）、安装状态（`cache/mo2/`）、已有归档模组资料和丢弃标记会保留，普通进度日志轮转不会删除这些历史；
- 本机数据（会话、缓存、日志、`settings.json`）均位于应用文件夹的 `cache/` 下；
- 删除 `cache\nexus-dashboard\session.json` 即清除登录态（下次需重新登录）。

## 许可

下载引擎参考 [Hex-Dragon/PCL2](https://github.com/Hex-Dragon/PCL2) 开源实现，详见 [LICENSE-NOTICE-CN.txt](LICENSE-NOTICE-CN.txt)。
