<#
.SYNOPSIS
Single launcher, FUSION MODE ONLY. start.cmd delegates here.
One console window. Every startup:
  - PCL engine reference check (ModNet.vb is downloaded from the PCL2
    GitHub repo and refreshed automatically when outdated; network
    failures are skipped - never blocks startup)
  - auto-build the C# engine host when missing or sources changed
  - dashboard backend (this window) + engine host (foreground)
Closing this window exits everything.
Pure ASCII on purpose (codepage-safe on any Windows locale).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$hostExe = Join-Path $root 'src\Downloader.Host\bin\Release\net8.0\Downloader.Host.exe'
$hostProj = Join-Path $root 'src\Downloader.Host\Downloader.Host.csproj'
$hostSrc = Join-Path $root 'src\Downloader.Host'
$coreSrc = Join-Path $root 'src\Downloader.Core'
$dashDir = Join-Path $root 'nexus-dashboard'
$dashPy = Join-Path $dashDir '.venv\Scripts\python.exe'

function Fail([string]$msg) {
    Write-Host ''
    Write-Host "[ERROR] $msg"
    Write-Host 'Exiting - start.cmd keeps this window open; press any key to close.'
    exit 1
}

function Update-ModNetReference {
    $refDir = Join-Path $root 'references'
    $refFile = Join-Path $refDir 'ModNet.vb'
    $stateFile = Join-Path $refDir '.modnet-status.txt'
    $headers = @{ 'User-Agent' = 'pcl-launcher' }
    try {
        $commitUrl = 'https://api.github.com/repos/Hex-Dragon/PCL2/commits?path=Plain%20Craft%20Launcher%202%2FModules%2FBase%2FModNet.vb&sha=main&per_page=1'
        $commit = @(Invoke-RestMethod -Uri $commitUrl -Headers $headers -TimeoutSec 8)[0]
        $sha = [string]$commit.sha
        $upToDate = $false
        if (Test-Path -LiteralPath $refFile) {
            $prev = ''
            try { $prev = (Get-Content -LiteralPath $stateFile -Raw -ErrorAction Stop).Trim() } catch { }
            if ($prev -eq $sha) { $upToDate = $true }
        }
        if ($upToDate) {
            Write-Host "[ENGINE] ModNet.vb reference up to date ($($sha.Substring(0, 7)))."
        }
        else {
            $contentUrl = 'https://api.github.com/repos/Hex-Dragon/PCL2/contents/Plain%20Craft%20Launcher%202%2FModules%2FBase%2FModNet.vb?ref=main'
            $item = Invoke-RestMethod -Uri $contentUrl -Headers $headers -TimeoutSec 8
            if ($item.encoding -ne 'base64') { throw "unexpected encoding: $($item.encoding)" }
            $bytes = [Convert]::FromBase64String(($item.content -replace "`n", '' -replace "`r", ''))
            if (-not (Test-Path -LiteralPath $refDir)) { New-Item -ItemType Directory -Path $refDir -Force | Out-Null }
            [IO.File]::WriteAllBytes($refFile, $bytes)
            Set-Content -LiteralPath $stateFile -Value $sha -Encoding ASCII
            Write-Host "[ENGINE] ModNet.vb reference updated ($($sha.Substring(0, 7)))."
        }
    }
    catch {
        Write-Host '[ENGINE] Reference check skipped (GitHub unreachable).'
    }
}

function Assert-DotnetSdk {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
        Fail 'dotnet not found. Install .NET SDK 8 from https://dotnet.microsoft.com/download and run again.'
    }
    $sdks = & dotnet --list-sdks 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sdks)) {
        Fail 'No .NET SDK found. Install the .NET SDK 8 and run again.'
    }
}

function Build-HostIfNeeded {
    $needBuild = -not (Test-Path -LiteralPath $hostExe)
    if (-not $needBuild) {
        $exeTime = (Get-Item -LiteralPath $hostExe).LastWriteTimeUtc
        $newest = Get-ChildItem -Path $hostSrc, $coreSrc -Recurse -File |
                  Where-Object { $_.Extension -in '.cs', '.csproj' } |
                  Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if ($newest -and $newest.LastWriteTimeUtc -gt $exeTime) { $needBuild = $true }
    }
    if ($needBuild) {
        Assert-DotnetSdk
        Write-Host '[BUILD] Building Downloader.Host (first run or sources changed) ...'
        Push-Location $appDir
        try {
            & dotnet build $hostProj -c Release
            if ($LASTEXITCODE -ne 0) { Fail "Build failed (exit $LASTEXITCODE)." }
        }
        finally { Pop-Location }
        if (-not (Test-Path -LiteralPath $hostExe)) { Fail "Build finished but executable not found: $hostExe" }
        Write-Host '[BUILD] Done.'
    }
}

function Remove-StalePort8000 {
    try {
        Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    }
    catch { }
}

function Start-Dashboard {
    $env:PIP_CACHE_DIR = Join-Path $root 'cache\nexus-dashboard\pip'
    foreach ($d in 'cache', 'cache\nexus-dashboard', 'cache\nexus-dashboard\mods',
                   'cache\nexus-dashboard\pip') {
        $dir = Join-Path $root $d
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    $py = 'python'
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        if (Get-Command py -ErrorAction SilentlyContinue) { $py = 'py -3' }
        else { Fail 'Python not found. Install Python 3.10+ and retry.' }
    }
    if (-not (Test-Path -LiteralPath $dashPy)) {
        Write-Host '[DASH] Creating venv .venv ...'
        Invoke-Expression "$py -m venv `"$dashDir\.venv`""
        if ($LASTEXITCODE -ne 0) { Fail 'Venv creation failed.' }
    }
    & $dashPy -c "import fastapi, uvicorn, playwright, bs4, dateutil, httpx" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[DASH] Installing dependencies ...'
        & $dashPy -m pip install -r (Join-Path $dashDir 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { Fail 'pip install failed.' }
    }
    Remove-StalePort8000
    Write-Host '[DASH] Starting backend: http://127.0.0.1:8000 (logs in this window)'
    $dash = Start-Process -FilePath $dashPy -ArgumentList 'app.py', '--port', '8000' -WorkingDirectory $dashDir -NoNewWindow -PassThru
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/api/status' -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        }
        catch { }
        if ($dash.HasExited) { break }
    }
    if ($ready) { Write-Host '[DASH] Backend ready.' }
    else { Write-Host '[DASH] Backend not ready within 30s (see logs above).' }
    try { Start-Process 'http://127.0.0.1:8000' } catch { }
    return $dash
}

function Start-Fusion {
    Build-HostIfNeeded
    $dash = Start-Dashboard
    Write-Host ''
    Write-Host '=== Downloader host (foreground) ==='
    Write-Host 'Open the dashboard, click [Open takeover window]: all nexusmods.com'
    Write-Host 'downloads in that window are handed to this 256-thread host.'
    Write-Host 'Closing this window exits everything.'
    Write-Host ''
    try {
        & $hostExe
    }
    finally {
        if ($dash -and -not $dash.HasExited) { Stop-Process -Id $dash.Id -Force }
    }
    Write-Host ''
    Write-Host '[END] Fusion exited.'
}

# ------------------------------------------------------------------- main
Update-ModNetReference
Start-Fusion
$null = Read-Host '[END] Press Enter to close'