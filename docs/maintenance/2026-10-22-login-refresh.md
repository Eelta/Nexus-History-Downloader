# 2026-10-22：Nexus 登录、刷新与发布维护经验

## 最终状态

- 发布版本：GitHub v0.0.8；应用内部版本 2.46。
- 代码提交：`2c5ff03`。安装包发布页：<https://github.com/Eelta/Nexus-History-Downloader/releases/tag/v0.0.8>。
- 安装包 SHA256：`9af7128b1d8a7d551b5e025e5828b0ba3f33ce64fe5eb68c65bee324148dd53d`。
- 本次按用户要求没有修改 README。维护经验放在本文件，避免混入产品说明。

## 登录状态不能只靠 Cookie 名称判断

旧代码先确认网页已登录，保存时却要求 Cookie 名称包含 `sid_`、`jwt` 或 `access_token`，导致登录成功仍报“未取得有效的 N 网登录信息”。此外，按几个首页 URL 查询 Cookie，会漏掉 `/users`、`/auth` 等路径下的 Cookie。

修复使用 `context.storage_state()` 读取完整状态，再仅保留 Nexus 域 Cookie 和 Nexus origin 的 localStorage。登录的判据仍是已就绪的站内登录页面；保存前后都检查，不能用 Cookie 存在代替登录成功，也不能把其他站点存储写入会话文件。失败时保留原状态，写入使用原子替换。

`has_session()` 只说明会话文件存在，所以界面改为“登录信息已保存”。它不证明当前登录有效，更不证明历史数据已加载完成。

## 安全验证与历史构建是不同问题

- 识别 `Performing security verification` 和 `website verifies you are not a bot` 等新验证页文案。
- 等待用户处理验证；不要自动刷新正在验证、输入或登录的页面。
- 优先复用已有 Nexus 标签页。导航超时后先检查已提交页面，不要不断另开标签页。
- 历史表格可能初始化后仍显示 `We are building your download history...`。必须等待真实行或完成后的明确空结果；不能把初始化的空表写入成功缓存。
- 现场曾出现三次历史页等待失败，随后一轮完整分页成功：站点表格 1,711 条，合并后 1,568 个 Skyrim 模组，筛选后 192 个。不能把这类加载失败笼统归为登录失败，也不能把一次恢复当作永久解决所有网络问题。

## 刷新状态和前端轮询

开始新的刷新任务时立即清除上一轮 `refresh_error`。否则页面会在新任务运行中继续显示旧错误，等待任务启动的轮询还可能提前退出。

前端运行中不展示上一轮刷新失败；启动轮询时记录当前结果版本，而不是等第一次 tick 才建立基线。快速任务可能在第一次 tick 前完成。登录状态轮询看到完成时，刷新任务也可能已经结束，需要补载模组列表。

保留已存在任务，合并刷新请求；不要为了重试取消正在扫描的浏览器。失败不覆盖旧模组列表、历史缓存或归档记录。

## 验证与发布

本次验证包括：138 项后端测试（2 项跳过）、前端虚拟时钟轮询测试、真实浏览器离线 Cookie/localStorage 往返恢复、DataTables 延迟加载和完整分页测试、打包后端隔离启动，以及真实站点完整历史读取。

主要回归入口：

```powershell
& nexus-dashboard/.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py'
node tests/status-polling.test.cjs
& nexus-dashboard/.venv/Scripts/python.exe tests/session-dom.test.py
& nexus-dashboard/.venv/Scripts/python.exe tests/history-dom.test.py
```

真实浏览器测试使用隔离上下文与拦截响应；不启动用户资料目录。现场诊断不向 Git 提交 HTML、会话、Cookie、带签名的下载 URL 或个人模组历史。

应用和安装器版本需同步，并保留旧数据版本兼容。验证 shell → backend、installer → app 两层附加 payload 的长度、标记和哈希；检查包内静态资源及应用版本与源码一致。上传 Release 后校验 GitHub 返回的文件大小和 SHA256，再确认发布及 latest 状态。

## 清理边界

最终运行文件是本机 `NHD/NHD.exe` 与 `dist/NHD.exe`。保留源码、测试、虚拟环境、构建依赖、用户会话、下载记录、归档和配置。此次临时诊断脚本、登录修复及状态修复构建目录、重复安装包、旧二进制备份、基准测试输出可在最终校验后清理。

其他轮次的构建目录可能仍是依赖来源，不能按“旧目录”批量删除。Windows 递归清理必须先检查每个绝对路径的归属及重解析点，使用原生 PowerShell 文件操作；清理后重新核对最终文件和用户数据哈希。
