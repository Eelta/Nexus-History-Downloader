# Nexus History Downloader（NHD）

基于 PCL 多线程下载引擎（参考其开源实现）的 Nexus Mods 工具：自包含的 Windows 桌面应用，
集「更新监测面板 + 多线程下载接管」于一体。源码用于构建安装包，Release 只发布一个文件。

## 功能

- **独立桌面界面**：无需浏览器，应用自带窗口加载本地仪表盘；
- **更新监测**：一次登录扫描 N 网全部下载历史，自动筛选「已下载且有更新」的模组，列表一次全量加载（无分页）；
- **多线程下载**：等效 PCL 引擎，HTTP Range 分段并行（默认 64 连接），源站不支持 Range 时自动降级单线程；
- **下载接管**：点击模组名打开受控浏览器窗口（使用真实登录态），窗口内所有 N 网下载自动转交引擎；
- **零维护**：缓存自动清理（残留分片、pip 缓存、日志限长），无需手动清理。

## 环境要求

- Windows 10/11（含 WebView2 运行时，Edge 自带）；
- 已安装 Microsoft Edge 或 Google Chrome（二选一，**不会自动下载浏览器**；两者皆无则无法登录/接管）。

## 构建与发布

```cmd
build-exe.cmd
```

- 一键产出 `dist\NHD.exe`（唯一安装包，内含应用本体）；
- **GitHub Release 只需发布这一个文件**；源码仓库不含任何构建产物。

## 安装与卸载

1. 运行 `NHD.exe`：选择安装文件夹，可选创建桌面快捷方式；
2. 安装完成后，从该文件夹运行 `NHD.exe`（应用与缓存数据同在该文件夹）；
3. 已注册为 Windows 应用：可在「设置 → 应用」中卸载（会删除安装文件夹全部内容，含下载成品，卸载前请确认）；
4. 若只想即拷即用：安装后整个文件夹可随意拷贝移动，即为便携版。

## 首次使用

1. 打开应用后在页面点「浏览器登录」，用 Edge/Chrome 真实资料登录一次（仅保存 N 网会话，不存密码）；
2. 列表中的模组即「已下载且有更新」的条目；点击模组名 → 接管窗口直达文件页 → 点任意下载自动转交引擎。

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
nexus-dashboard\       FastAPI 仪表盘（Python 后端）
tests\                 引擎自检
dist\                  编译产物（NHD.exe）
cache\                 本地开发/运行数据（会话 / 缓存 / 下载成品），不入库
```

## 常用配置

- **下载目录**：应用内顶栏修改，或编辑应用旁 `settings.json`（`{"DefaultDownloadDir":"D:\\Downloads"}`）；
- **并发线程**：应用内顶栏滑块实时调整（8~256，默认 64）；环境变量 `CUSTOMDL_MAX_THREADS` 可设初始值；
- **并行任务**：环境变量 `CUSTOMDL_MAX_JOBS`（默认 5）；
- **宿主端口**：环境变量 `CUSTOMDL_HOST_PORT`（默认 18765）。

## 缓存与隐私

- 自动清理：残留下载分片（>24h）、pip 缓存、日志（限约 2000 行）；**下载成品与登录会话永不自动删除**；
- 本机数据（会话、缓存、日志、`settings.json`）均位于应用文件夹的 `cache/` 下，已被 `.gitignore` 排除，不会上传 GitHub；
- 删除 `cache\nexus-dashboard\session.json` 即清除登录态（下次需重新登录）。

## 许可

下载引擎参考 [Hex-Dragon/PCL2](https://github.com/Hex-Dragon/PCL2) 开源实现，详见 [LICENSE-NOTICE.txt](LICENSE-NOTICE.txt)。