# Nexus History Downloader

## Features

- **Standalone desktop UI**: No browser needed — the app loads a local dashboard in its own window;
- **Update monitoring**: One login scans all of your Nexus Mods download history, automatically filters for mods that are "already downloaded and have updates", and loads the full list at once (no pagination);
- **Multi-threaded downloads**: A PCL-equivalent engine using segmented HTTP Range parallel downloads (64 connections by default), automatically falling back to a single thread when the source does not support Range;
- **Download takeover**: Clicking a mod name opens a controlled browser window (using your real login session); every Nexus Mods download in that window is automatically handed off to the engine;
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

## First Use

1. After opening the app, click "Browser Login" on the page and log in once with a real Edge/Chrome profile (only the Nexus Mods session is saved; no passwords are stored);
2. The mods in the list are the entries that are "already downloaded and have updates"; click a mod name → a takeover window opens directly on its files page → clicking any download automatically hands it to the engine.

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
nexus-dashboard\       FastAPI dashboard (Python backend)
tests\                 Engine self-checks
dist\                  Build output (NHD.exe)
```

## Common Configuration

- **Download directory**: change it in the app's top bar, or edit `settings.json` next to the app (`{"DefaultDownloadDir":"D:\\Downloads"}`);
- **Concurrent threads**: adjust in real time with the top-bar slider (8–256, default 64); the `CUSTOMDL_MAX_THREADS` environment variable can set the initial value;
- **Parallel jobs**: the `CUSTOMDL_MAX_JOBS` environment variable (default 5);
- **Host port**: the `CUSTOMDL_HOST_PORT` environment variable (default 18765).

## Cache & Privacy

- Automatic cleanup: leftover download chunks (>24h), pip cache, logs (capped at about 2000 lines);
- Local data (session, cache, logs, `settings.json`) all lives under `cache/` in the application folder;
- Deleting `cache\nexus-dashboard\session.json` clears the login state (you will need to log in again next time).

## License

The download engine is based on the open-source implementation of [Hex-Dragon/PCL2](https://github.com/Hex-Dragon/PCL2); see [LICENSE-NOTICE.txt](LICENSE-NOTICE.txt).