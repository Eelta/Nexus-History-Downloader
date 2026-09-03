@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  One-click build: produce the install package
rem    dist\NHD.exe  (single file)
rem  Layout of the package (appended by this script):
rem    [NHD install core][NHD.exe app][len][magic]
rem  The app itself carries the Python backend + C# host:
rem    [NHD shell][nexus-backend.exe][len][magic]
rem  Requires .NET SDK 8+, Edge/Chrome (never downloaded).
rem ============================================================

set "VENV_PY=%~dp0nexus-dashboard\.venv\Scripts\python.exe"
set "DIST=%~dp0dist"
set "BUILD=%~dp0build"
set "HOST_OUT=%BUILD%\_host_publish"
set "BACKEND_OUT=%BUILD%\_backend_publish"
set "SHELL_OUT=%BUILD%\_shell_publish"
set "SETUP_OUT=%BUILD%\_setup_publish"

echo [1/6] Preparing Python environment ...
if not exist "%VENV_PY%" (
  where py >nul 2>nul
  if not errorlevel 1 (py -3 -m venv "%~dp0nexus-dashboard\.venv") else (python -m venv "%~dp0nexus-dashboard\.venv")
  if errorlevel 1 goto :fail
)
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 goto :fail
"%VENV_PY%" -m pip install -r "%~dp0nexus-dashboard\requirements.txt" pyinstaller
if errorlevel 1 goto :fail

echo [2/6] Building C# download host (win-x64 self-contained single file) ...
if exist "%HOST_OUT%" rmdir /s /q "%HOST_OUT%"
dotnet publish "%~dp0src\Downloader.Host\Downloader.Host.csproj" -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o "%HOST_OUT%"
if errorlevel 1 goto :fail

echo [3/6] Packaging Python backend (nexus-backend, host embedded) ...
if exist "%BACKEND_OUT%" rmdir /s /q "%BACKEND_OUT%"
"%VENV_PY%" -m PyInstaller --noconfirm --clean --onefile --console ^
  --name nexus-backend ^
  --icon "%~dp0assets\icon.ico" ^
  --add-data "%~dp0nexus-dashboard\static;static" ^
  --collect-all playwright ^
  --add-binary "%HOST_OUT%\Downloader.Host.exe;." ^
  --distpath "%BACKEND_OUT%" --workpath "%BUILD%\pyinstaller" --specpath "%BUILD%" ^
  "%~dp0nexus-dashboard\app.py"
if errorlevel 1 goto :fail

echo [4/6] Building desktop app shell (WebView2) ...
if exist "%SHELL_OUT%" rmdir /s /q "%SHELL_OUT%"
dotnet publish "%~dp0src\AppShell\AppShell.csproj" -c Release -r win-x64 --self-contained true ^
  -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o "%SHELL_OUT%"
if errorlevel 1 goto :fail

echo [5/6] Assembling app: NHD.exe = shell + backend payload ...
powershell -NoProfile -Command "$o=[IO.File]::Create('%BUILD%\NHD.exe');$s=[IO.File]::OpenRead('%SHELL_OUT%\NHD.exe');$s.CopyTo($o);$s.Close();$p=[IO.File]::OpenRead('%BACKEND_OUT%\nexus-backend.exe');$l=$p.Length;$p.CopyTo($o);$p.Close();$o.Write([BitConverter]::GetBytes([int64]$l),0,8);$m=[Text.Encoding]::ASCII.GetBytes('NEXUSPAYLOAD');$o.Write($m,0,$m.Length);$o.Close()"
if errorlevel 1 goto :fail

echo [6/6] Building installer and assembling NHD.exe ...
if exist "%SETUP_OUT%" rmdir /s /q "%SETUP_OUT%"
dotnet publish "%~dp0src\Installer\Installer.csproj" -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o "%SETUP_OUT%"
if errorlevel 1 goto :fail
if not exist "%DIST%" mkdir "%DIST%"
powershell -NoProfile -Command "$o=[IO.File]::Create('%DIST%\NHD.exe');$s=[IO.File]::OpenRead('%SETUP_OUT%\NHD.exe');$s.CopyTo($o);$s.Close();$p=[IO.File]::OpenRead('%BUILD%\NHD.exe');$l=$p.Length;$p.CopyTo($o);$p.Close();$o.Write([BitConverter]::GetBytes([int64]$l),0,8);$m=[Text.Encoding]::ASCII.GetBytes('NEXUSSETUPPAYLOAD');$o.Write($m,0,$m.Length);$o.Close()"
if errorlevel 1 goto :fail
if exist "%~dp0LICENSE-NOTICE.txt" copy /y "%~dp0LICENSE-NOTICE.txt" "%DIST%\LICENSE-NOTICE.txt" >nul
del /q "%BUILD%\NHD.exe"
if exist "%HOST_OUT%" rmdir /s /q "%HOST_OUT%"
if exist "%BACKEND_OUT%" rmdir /s /q "%BACKEND_OUT%"
if exist "%SHELL_OUT%" rmdir /s /q "%SHELL_OUT%"
if exist "%SETUP_OUT%" rmdir /s /q "%SETUP_OUT%"

echo.
echo Build done: %DIST%\NHD.exe  (single install package)
echo Run it to install NHD.exe + cache into a folder of
echo your choice, with an optional desktop shortcut and Windows
echo uninstall entry.
goto :eof

:fail
echo.
echo BUILD FAILED - see errors above.
exit /b 1