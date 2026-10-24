<#
.SYNOPSIS
  One-step setup for Video Studio: WSL2 distro, Linux recovery tools, Windows media
  tools, and the hash-locked Python packages.

.DESCRIPTION
  Safe to re-run: anything already installed is skipped, and it ends with a report.
    setup_studio.bat              check and install what is missing
    setup_studio.bat -CheckOnly   only report, change nothing
    setup_studio.bat -Distro Debian

  Linux packages are installed as root through `wsl -u root`, so no sudo password is
  asked. Windows tools come from winget, which may show its own prompts (usbipd-win
  needs one UAC approval). Python packages go into .venv in this folder, which the
  launchers use automatically, so your global Python is left alone.
#>
param(
    [string]$Distro = '',
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot  # project root (this script lives in scripts\)
$env:WSL_UTF8 = '1'
$Results = New-Object System.Collections.ArrayList

function Add-Result([string]$Name, [bool]$Ok, [string]$Detail) {
    [void]$Results.Add([pscustomobject]@{ Component = $Name; Status = $(if ($Ok) { 'OK' } else { 'MISSING' }); Detail = $Detail })
}

function Write-Step([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }

function Get-ConfiguredDistro {
    $envFile = Join-Path $Root '.env'
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*WSL_DISTRO_NAME\s*=\s*(.+?)\s*$') { return $Matches[1].Trim('"', "'") }
        }
    }
    return 'Ubuntu'
}

function Update-SessionPath {
    # Append PATH entries added since this window opened (e.g. by an earlier winget run).
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$env:Path;$machine;$user"
}

function Clean-WslLines($lines) {
    return @($lines | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
}

Update-SessionPath
if (-not $Distro) { $Distro = Get-ConfiguredDistro }
$mode = $(if ($CheckOnly) { 'check only, nothing will be changed' } else { 'check and install' })
Write-Host "Video Studio setup ($mode). WSL distro: $Distro"

# --------------------------------------------------------------------------- WSL
Write-Step 'WSL2 and the Linux distro'
$wslReady = $false
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Add-Result 'WSL' $false 'Not installed. Run in an admin PowerShell: wsl --install   (then reboot and re-run this setup)'
} else {
    $distros = Clean-WslLines (wsl.exe -l -q 2>$null)
    if ($distros -notcontains $Distro) {
        Add-Result "WSL distro '$Distro'" $false "Not installed. Run: wsl --install -d $Distro   (create the Linux user when asked, then re-run this setup)"
    } else {
        $verbose = Clean-WslLines (wsl.exe -l -v 2>$null)
        $row = $verbose | Where-Object { ($_ -replace '^\*\s*', '') -match "^$([regex]::Escape($Distro))\s" } | Select-Object -First 1
        $version = $(if ($row) { ($row -split '\s+')[-1] } else { '?' })
        if ($version -eq '1') {
            Add-Result "WSL distro '$Distro'" $false "Runs as WSL1; USB passthrough needs WSL2. Run: wsl --set-version $Distro 2"
        } else {
            Add-Result "WSL distro '$Distro'" $true "installed (WSL$version)"
            $wslReady = $true
        }
    }
}

# ------------------------------------------------------------- Linux recovery tools
Write-Step 'Linux recovery tools inside WSL'
# tool -> Debian/Ubuntu package that provides it
$linuxTools = [ordered]@{ ddrescue = 'gddrescue'; photorec = 'testdisk'; testdisk = 'testdisk'; flock = 'util-linux'; stdbuf = 'coreutils' }

function Get-MissingLinuxTools {
    $names = ($linuxTools.Keys -join ' ')
    $probe = "for t in $names; do command -v `$t >/dev/null 2>&1 || echo `$t; done"
    return Clean-WslLines (wsl.exe -d $Distro -u root -e sh -c $probe 2>$null)
}

if ($wslReady) {
    $missing = Get-MissingLinuxTools
    if ($missing.Count -gt 0 -and -not $CheckOnly) {
        $hasApt = Clean-WslLines (wsl.exe -d $Distro -u root -e sh -c 'command -v apt-get' 2>$null)
        if ($hasApt.Count -eq 0) {
            Write-Host "This distro has no apt-get; install these yourself: $($missing -join ', ')" -ForegroundColor Yellow
        } else {
            $packages = @($missing | ForEach-Object { $linuxTools[$_] } | Sort-Object -Unique)
            Write-Host "Installing in WSL: $($packages -join ' ')"
            wsl.exe -d $Distro -u root -e sh -c "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y $($packages -join ' ')"
            $missing = Get-MissingLinuxTools
        }
    }
    foreach ($tool in $linuxTools.Keys) {
        $ok = $missing -notcontains $tool
        Add-Result "Linux: $tool" $ok $(if ($ok) { 'found' } else { "apt package: $($linuxTools[$tool])" })
    }
} else {
    Add-Result 'Linux tools' $false 'Skipped until the WSL distro is ready'
}

# ------------------------------------------------------------------ Windows tools
Write-Step 'Windows tools'
$windowsTools = @(
    @{ Name = 'usbipd-win (USB to WSL)'; Id = 'dorssel.usbipd-win'; Command = 'usbipd'; Paths = @("$env:ProgramFiles\usbipd-win\usbipd.exe") },
    @{ Name = 'ffmpeg (cut, join, repair)'; Id = 'Gyan.FFmpeg'; Command = 'ffmpeg'; Paths = @("$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe") },
    @{ Name = 'HandBrakeCLI (transcode)'; Id = 'HandBrake.HandBrake.CLI'; Command = 'HandBrakeCLI'; Paths = @("$env:ProgramFiles\HandBrake\HandBrakeCLI.exe", "${env:ProgramFiles(x86)}\HandBrake\HandBrakeCLI.exe") }
)

function Find-WindowsTool($tool) {
    $cmd = Get-Command $tool.Command -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    foreach ($path in $tool.Paths) { if ($path -and (Test-Path $path)) { return $path } }
    return $null
}

$hasWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
foreach ($tool in $windowsTools) {
    $found = Find-WindowsTool $tool
    if (-not $found -and -not $CheckOnly) {
        if ($hasWinget) {
            Write-Host "Installing $($tool.Name) with winget..."
            winget install --id $tool.Id -e
            Update-SessionPath
            $found = Find-WindowsTool $tool
        } else {
            Write-Host "winget is not available; install $($tool.Name) manually." -ForegroundColor Yellow
        }
    }
    Add-Result $tool.Name ([bool]$found) $(if ($found) { $found } else { "winget install --id $($tool.Id) -e" })
}

# ---------------------------------------------------------------- Python packages
Write-Step 'Python packages (hash-locked)'
$venvPython = Join-Path $Root '.venv\Scripts\python.exe'
$lock = Join-Path $Root 'requirements.lock'
$imports = 'import fastapi, uvicorn, pydantic, aiosqlite'

function Test-PythonVersion($exe) {
    & $exe -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
    return $LASTEXITCODE -eq 0
}

$basePython = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $basePython) {
    Add-Result 'Python 3.10+' $false 'Not found on PATH. Install from python.org (tick "Add to PATH") or: winget install Python.Python.3.13'
} elseif (-not (Test-PythonVersion $basePython.Source)) {
    Add-Result 'Python 3.10+' $false "Found $($basePython.Source), but it is older than 3.10"
} else {
    Add-Result 'Python 3.10+' $true $basePython.Source
    if (-not $CheckOnly) {
        if (-not (Test-Path $venvPython)) {
            Write-Host 'Creating .venv ...'
            & $basePython.Source -m venv (Join-Path $Root '.venv')
        }
        & $venvPython -m pip install --require-hashes -r $lock
    }
    $venvOk = $false
    if (Test-Path $venvPython) {
        & $venvPython -c $imports 2>$null
        $venvOk = $LASTEXITCODE -eq 0
    }
    if ($venvOk) {
        Add-Result 'App packages (.venv)' $true 'installed from requirements.lock'
    } else {
        & $basePython.Source -c $imports 2>$null
        if ($LASTEXITCODE -eq 0) {
            Add-Result 'App packages' $true 'found in your global Python (run setup without -CheckOnly to use a locked .venv instead)'
        } else {
            Add-Result 'App packages' $false 'Run this setup without -CheckOnly to install them into .venv'
        }
    }
}

# ------------------------------------------------------------------------ Report
Write-Step 'Summary'
$Results | Format-Table -AutoSize -Wrap | Out-String -Width 200 | Write-Host
$missingCount = @($Results | Where-Object { $_.Status -ne 'OK' }).Count
if ($missingCount -eq 0) {
    Write-Host 'Everything is ready. Start the app with start_studio.bat.' -ForegroundColor Green
    exit 0
}
Write-Host "$missingCount item(s) need attention - see the Detail column above." -ForegroundColor Yellow
exit 1
