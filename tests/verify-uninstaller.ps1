param(
    [Parameter(Mandatory = $true)]
    [string]$UninstallerPath
)

# Run with Windows PowerShell: powershell -NoProfile -File tests\verify-uninstaller.ps1 -UninstallerPath <compiled exe>
# Only disposable fixtures beneath this repository's .verify directory are removed.
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd('\')
$verifyRoot = [IO.Path]::GetFullPath((Join-Path $repository '.verify'))
$fixtureRoot = [IO.Path]::GetFullPath((Join-Path $verifyRoot ('uninstaller-' + [Guid]::NewGuid().ToString('N'))))

function Assert-FixturePath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (!$absolute.StartsWith($verifyRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the repository .verify directory: $absolute"
    }
    for ($ancestor = [IO.DirectoryInfo]::new($absolute); $null -ne $ancestor; $ancestor = $ancestor.Parent) {
        if ($ancestor.Exists -and ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing a fixture path with a linked ancestor: $($ancestor.FullName)"
        }
    }
}

function New-FixtureInstallation([string]$Path) {
    Assert-FixturePath $Path
    [IO.Directory]::CreateDirectory($Path) | Out-Null
    [IO.File]::WriteAllText((Join-Path $Path 'NHD.exe'), 'test fixture, not an executable')
    [IO.File]::WriteAllText((Join-Path $Path 'Uninstall.exe'), 'test fixture, not an executable')
}

function Get-UninstallRegistrySnapshot {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Uninstall\NHD')
    if ($null -eq $key) { return '<absent>' }
    try {
        $values = @($key.GetValueNames() | Sort-Object | ForEach-Object {
            [PSCustomObject]@{ Name = $_; Kind = [string]$key.GetValueKind($_); Value = $key.GetValue($_) }
        })
        return ConvertTo-Json -InputObject $values -Compress -Depth 5
    } finally { $key.Dispose() }
}

$assembly = [Reflection.Assembly]::Load([IO.File]::ReadAllBytes([IO.Path]::GetFullPath($UninstallerPath)))
$program = $assembly.GetType('NexusUninstall.Program', $true)
$validate = $program.GetMethod('ValidateInstallDirectory', [Reflection.BindingFlags]'NonPublic, Static')
if ($null -eq $validate -or $null -eq $program.GetMethod('RunUninstall')) { throw 'Uninstaller entry points were not found.' }

function Assert-RejectedInstallation([string]$Path) {
    $rejected = $false
    try { $validate.Invoke($null, @($Path)) | Out-Null }
    catch {
        if ($_.Exception.GetBaseException() -is [InvalidOperationException]) { $rejected = $true }
        else { throw }
    }
    if (!$rejected) { throw "Unsafe installation path was accepted: $Path" }
}

$registryBefore = Get-UninstallRegistrySnapshot
$junction = $null
try {
    Assert-FixturePath $fixtureRoot
    [IO.Directory]::CreateDirectory($fixtureRoot) | Out-Null
    Assert-RejectedInstallation ([IO.Path]::GetPathRoot($fixtureRoot))

    $wrongName = Join-Path $fixtureRoot 'wrong-folder-name'
    New-FixtureInstallation $wrongName
    Assert-RejectedInstallation $wrongName
    if (!(Test-Path -LiteralPath (Join-Path $wrongName 'NHD.exe'))) { throw 'Rejected fixture was modified.' }

    $installation = Join-Path $fixtureRoot 'NHD'
    New-FixtureInstallation $installation
    $nested = Join-Path $installation 'cache\nested'
    [IO.Directory]::CreateDirectory($nested) | Out-Null
    [IO.File]::WriteAllText((Join-Path $nested 'download.txt'), 'disposable fixture')
    $sentinelRoot = Join-Path $fixtureRoot 'outside-installation'
    [IO.Directory]::CreateDirectory($sentinelRoot) | Out-Null
    $sentinel = Join-Path $sentinelRoot 'keep.txt'
    [IO.File]::WriteAllText($sentinel, 'must survive uninstall')
    $junction = Join-Path $installation 'cache\external'
    try {
        New-Item -ItemType Junction -Path $junction -Target $sentinelRoot -ErrorAction Stop | Out-Null
        if (!([IO.File]::GetAttributes($junction) -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Junction creation did not produce a reparse point.'
        }
        Write-Host 'Junction fixture created; its target must remain untouched.'
    } catch {
        if (Test-Path -LiteralPath $junction) { throw }
        $junction = $null
        Write-Host ('SKIP junction fixture: ' + $_.Exception.Message)
    }

    Assert-FixturePath $installation
    # This impossible launcher PID has already exited; never invokes the real Uninstall.exe.
    [NexusUninstall.Program]::RunUninstall([string]$installation, [int]::MaxValue)
    if (Test-Path -LiteralPath $installation) { throw 'Installation fixture was not removed recursively.' }
    if ([IO.File]::ReadAllText($sentinel) -ne 'must survive uninstall') { throw 'Junction target was modified.' }

    # Exercise the real executable -> hidden PowerShell -> in-memory assembly flow as well.
    # Spaces and shell metacharacters in this path must remain ordinary path data.
    $launchedInstallation = Join-Path $fixtureRoot "worker ' & test\NHD"
    New-FixtureInstallation $launchedInstallation
    $launchedUninstaller = Join-Path $launchedInstallation 'Uninstall.exe'
    [IO.File]::Copy([IO.Path]::GetFullPath($UninstallerPath), $launchedUninstaller, $true)
    Assert-FixturePath $launchedInstallation
    $launcher = Start-Process -FilePath $launchedUninstaller -WindowStyle Hidden -PassThru `
        -ArgumentList @('--uninstall', ('"{0}"' -f $launchedInstallation))
    try {
        if (!$launcher.WaitForExit(15000)) { throw 'Uninstaller launcher did not exit.' }
        if ($launcher.ExitCode -ne 0) { throw "Uninstaller launcher failed with exit code $($launcher.ExitCode)." }
    } finally { $launcher.Dispose() }
    $workerWait = [Diagnostics.Stopwatch]::StartNew()
    while ((Test-Path -LiteralPath $launchedInstallation) -and $workerWait.ElapsedMilliseconds -lt 15000) {
        Start-Sleep -Milliseconds 100
    }
    if (Test-Path -LiteralPath $launchedInstallation) { throw 'Hidden uninstall worker did not remove the fixture.' }
    if ((Get-UninstallRegistrySnapshot) -ne $registryBefore) { throw 'Real uninstall registration changed.' }
    Write-Host 'PASS: unsafe paths rejected, recursive deletion and hidden worker succeeded, external sentinel and registration preserved.'
} finally {
    # Detach any surviving link before fixture cleanup, including on a test failure.
    if ($null -ne $junction -and (Test-Path -LiteralPath $junction)) {
        $junctionFull = [IO.Path]::GetFullPath($junction)
        if (!$junctionFull.StartsWith($fixtureRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Refusing to remove a junction outside the fixture.'
        }
        [IO.Directory]::Delete($junctionFull, $false)
    }
    if (Test-Path -LiteralPath $fixtureRoot) {
        Assert-FixturePath $fixtureRoot
        Remove-Item -LiteralPath $fixtureRoot -Recurse -Force
    }
}
