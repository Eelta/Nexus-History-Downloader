# Nexus History Downloader

**English** | [简体中文](README-CN.md)

## Features

- **Standalone desktop UI**: No browser needed — the app loads a local dashboard in its own window;
- **Update monitoring**: One login scans all of your Nexus Mods download history, compares each update with the most recent download, and excludes mods downloaded after the update; caches using the previous timezone format are automatically invalidated and rescanned, and the full list loads at once (no pagination);
- **Multi-threaded downloads**: A PCL-equivalent engine using segmented HTTP Range parallel downloads (256 connections by default, the slider maximum), automatically falling back to a single thread when the source does not support Range;
- **Download takeover**: Clicking a mod name opens a controlled browser window (using your real login session); every Nexus Mods download in that window is automatically handed off to the engine;
- **Install through MO2**: Completed downloads enter a persistent installation queue. Review or edit the suggested installed-mod name, then use MO2's normal installer, including FOMOD options and merge/replace prompts. Optional archive deletion runs only after MO2 reports success;
- **Zero maintenance**: Caches are cleaned up automatically (leftover chunks, pip cache, capped log length).

## Requirements

- Windows 10/11 (including the WebView2 runtime, which comes with Edge);
- Microsoft Edge or Google Chrome installed (either one; the app will **not download a browser automatically** — without either, login/takeover is not possible).

## Build & Release

```cmd
build-exe.cmd
```

- Produces `dist\NHD.exe` in one step (contains the app itself);

## Installation & Uninstallation

1. Run `NHD.exe`: choose an installation folder, optionally create a desktop shortcut;
2. After installation, run `NHD.exe` from that folder (the app and its cache data stay in the same folder);
3. It is registered as a Windows app: you can uninstall it via Settings → Apps (this deletes everything in the installation folder — please confirm before uninstalling).

The uninstaller uses Windows' built-in .NET Framework and PowerShell and occupies only a few tens of KB, without copying the entire installer package.

## First Use

1. After opening the app, click "Browser Login" on the page and log in once with a real Edge/Chrome profile (only the Nexus Mods session is saved; no passwords are stored);
2. The mods in the list are the entries that are "already downloaded and have updates"; click a mod name → a takeover window opens directly on its files page → clicking any download automatically hands it to the engine.

## Install Downloaded Mods into MO2

1. Open Settings → MO2 and choose the folder containing `ModOrganizer.exe`. The default is `F:\Skyrim\iniRePather-Skyrim`; you can change it. The choice saves automatically and configures the bundled bridge plugin. Open or restart MO2 the first time.
2. Leave automatic installation enabled to send completed downloads to the confirmation queue. The app suggests existing MO2 mod names using Nexus IDs, names, and available archive file listings compared with installed file paths. These are suggestions: every package requires you to confirm the name, choose another candidate, or enter your own name.
3. Click the confirmation/install button and finish the native MO2 installer. FOMOD steps, optional components, and handling of an existing mod with the same name remain under your control in MO2.
4. Optionally enable deletion after installation. Only the successfully installed download is removed; cancellation, failure, or skipping keeps its archive. The default is to keep archives.

Existing archived mod metadata and discard marks are retained. Download filenames are made unique when a file already exists. Completed download records and the installation queue survive app restarts; installing a mod does not erase its recorded metadata.

The gray/black workspace starts maximized, with configuration in Settings. Search updates instantly; `/` focuses search and `Ctrl+,` opens Settings. Background images default to `E:\BaiduSyncdisk\壁纸图片` and randomly crossfade every five minutes while the app runs. Change the folder, transparency, or enable switch in Settings → Background; higher transparency makes images fainter.

Settings apply and save automatically. Paths save after a brief typing pause, and switches apply immediately. Settings → Appearance adjusts text size from 11 to 22 with live preview. Text size and concurrency persist across restarts; concurrency changes apply to new downloads.

## Run from Source (Development)

```powershell
powershell -ExecutionPolicy Bypass -File start.ps1   # one-click start (build engine + dashboard)
python nexus-dashboard\app.py --demo                 # offline demo
python nexus-dashboard\self_check.py                 # offline self-check
```

## Directory Structure

```
build-exe.cmd          One-click build of the installer package (engine + backend + desktop shell + installer merged)
src\Downloader.Core    Download engine
src\Downloader.Host    Download host (HTTP service, called by the dashboard)
src\AppShell           Desktop shell (WebView2 window, includes backend extraction)
src\Installer          Installer (GUI wizard + uninstall registration)
src\Uninstaller        Lightweight uninstaller (uses the Windows runtime)
nexus-dashboard\       FastAPI dashboard (Python backend)
tests\                 Engine self-checks
dist\                  Build output (NHD.exe)
```

## Common Configuration

- **Download directory**: change it in Settings → Downloads, or edit `settings.json` next to the app (`{"DefaultDownloadDir":"D:\\Downloads"}`);
- **Concurrent threads**: adjust in Settings → Downloads (8–256, default 256); the `CUSTOMDL_MAX_THREADS` environment variable can set the initial value;
- **MO2 installation**: customize the MO2 directory, automatic handoff, and archive cleanup in Settings → MO2; these settings are saved alongside the existing download settings;
- **Parallel jobs**: the `CUSTOMDL_MAX_JOBS` environment variable (default 5);
- **Host port**: the `CUSTOMDL_HOST_PORT` environment variable (default 18765).

## Cache & Privacy

- Automatic cleanup: leftover download chunks (>24h), pip cache, logs (capped at about 2000 lines);
- Completed download history (`cache/takeover-history.jsonl`), installation state (`cache/mo2/`), archived mod details, and discard marks are preserved; ordinary progress-log rotation does not delete this history;
- Local data (session, cache, logs, `settings.json`) all lives under `cache/` in the application folder;
- Deleting `cache\nexus-dashboard\session.json` clears the login state (you will need to log in again next time).

## License

The download engine is based on the open-source implementation of [Hex-Dragon/PCL2](https://github.com/Hex-Dragon/PCL2); see [LICENSE-NOTICE.txt](LICENSE-NOTICE.txt).
