#Requires -Version 5.1
<#
.SYNOPSIS
  One-time setup for the Ultra Productivity Suite on any Windows 10/11 PC.
  Double-click Setup.cmd instead of running this directly.
.DESCRIPTION
  1. Installs Python and LibreHardwareMonitor with winget if they are missing
  2. Installs the Python packages the suite needs
  3. Creates .env with values detected on this PC (router IP, a private token, sensor path)
  4. Installs the Ultra Dock (local build, or downloaded from the GitHub release)
  5. Turns on start-at-sign-in (one Windows admin prompt, for the hardware sensors)
  6. Starts everything and opens the dashboards
  Safe to run again at any time; it only fills in what's missing.
#>
param([switch]$FromLauncher, [switch]$NoAutostart, [switch]$NoStart)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$Root = $PSScriptRoot
$Repo = "jeemwla/ultra-productivity-suite"   # where the dock download comes from
$Step = 0
function Step([string]$t) { $script:Step++; Write-Host ""; Write-Host "  [$script:Step/6] $t" -ForegroundColor Cyan }
function Ok([string]$t) { Write-Host "        $t" -ForegroundColor Green }
function Warn([string]$t) { Write-Host "        $t" -ForegroundColor Yellow }
function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }
function Have([string]$cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Winget-Install([string]$id) {
    if (-not (Have winget)) { throw "winget is missing. Install 'App Installer' from the Microsoft Store, then run Setup again." }
    winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements --disable-interactivity | Out-Null
    Refresh-Path
}

Write-Host ""
Write-Host "  Ultra Productivity Suite setup" -ForegroundColor Cyan
Write-Host "  ------------------------------" -ForegroundColor DarkCyan

# 1. Tools ------------------------------------------------------------------
Step "Checking tools"
$pyOk = $false
if (Have python) { try { $v = (python --version) 2>&1; $pyOk = "$v" -match "Python 3\.(1[0-9]|[2-9][0-9])" } catch {} }
if (-not $pyOk) { Warn "Installing Python 3.13 ..."; Winget-Install "Python.Python.3.13"; }
if (-not (Have python)) { throw "Python install did not finish. Restart the PC and run Setup again." }
Ok ((python --version) 2>&1)

$lhmExe = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter LibreHardwareMonitor.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
if (-not $lhmExe) { $lhmExe = (Get-Command LibreHardwareMonitor -ErrorAction SilentlyContinue).Source }
if (-not $lhmExe) {
    Warn "Installing LibreHardwareMonitor (hardware sensors) ..."
    Winget-Install "LibreHardwareMonitor.LibreHardwareMonitor"
    $lhmExe = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter LibreHardwareMonitor.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if ($lhmExe) {
    $cfg = Join-Path (Split-Path $lhmExe) "LibreHardwareMonitor.config"
    if (-not (Test-Path $cfg)) {
        @'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <appSettings>
    <add key="runWebServerMenuItem" value="true" />
    <add key="listenerPort" value="8085" />
    <add key="startMinMenuItem" value="true" />
    <add key="minTrayMenuItem" value="true" />
  </appSettings>
</configuration>
'@ | Set-Content -Path $cfg -Encoding UTF8
    }
    Ok "LibreHardwareMonitor ready (web server on port 8085)"
} else { Warn "LibreHardwareMonitor not installed; PC Sentinel will show sensors as offline." }

# 2. Python packages ----------------------------------------------------------
Step "Installing Python packages"
python -m pip install --disable-pip-version-check -q --upgrade pip 2>$null | Out-Null
python -m pip install --disable-pip-version-check -q -r "$Root\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install failed. Check your internet connection and run Setup again." }
Ok "Done"

# 3. .env ---------------------------------------------------------------------
Step "Configuring this PC"
$envPath = Join-Path $Root ".env"
if (-not (Test-Path $envPath)) { Copy-Item "$Root\.env.example" $envPath }
$lines = [System.Collections.Generic.List[string]](Get-Content $envPath)
function Set-EnvValue([string]$key, [string]$value, [switch]$OnlyIfEmpty) {
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$key=(.*)$") {
            if ($OnlyIfEmpty -and $Matches[1].Trim()) { return }
            $lines[$i] = "$key=$value"; return
        }
    }
    $lines.Add("$key=$value")
}
$token = -join ((48..57) + (97..102) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
Set-EnvValue "BRIDGE_TOKEN" $token -OnlyIfEmpty
$gw = (Get-NetRoute -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1).NextHop
if ($gw) { Set-EnvValue "GLINET_ROUTER_IP" $gw -OnlyIfEmpty }
if ($lhmExe) { Set-EnvValue "LHM_EXE" $lhmExe }
[System.IO.File]::WriteAllLines($envPath, $lines)
Ok "Router: $gw   Sensors: $(if ($lhmExe) { 'found' } else { 'missing' })   Private token: created"
Ok "Pick your speakers any time in the alarm app under Sound."

# 4. Dock ---------------------------------------------------------------------
Step "Installing Ultra Dock and deck knobs"
$dockDir = Join-Path $env:LOCALAPPDATA "UltraSuite"; New-Item -ItemType Directory -Force $dockDir | Out-Null
$dockDst = Join-Path $dockDir "ultra-dock.exe"
$local = "$Root\02_translucent_taskbar_dock\src-tauri\target\release\ultra-dock.exe"
Get-Process ultra-dock -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
if (Test-Path $local) { Copy-Item $local $dockDst -Force; Ok "Installed from local build" }
elseif ($Repo -notlike "OWNER/*") {
    try {
        Invoke-WebRequest -UseBasicParsing "https://github.com/$Repo/releases/latest/download/ultra-dock.exe" -OutFile $dockDst
        Ok "Downloaded from github.com/$Repo"
    } catch { Warn "Couldn't download the dock ($($_.Exception.Message)). The rest of the suite still works." }
} else { Warn "No dock build found; skipping." }
if (Test-Path "$env:APPDATA\deck\deckDeck") {
    python -I "$Root\03_deck_deck_config\generate_deck_profile.py" --restart
    Ok "deck deck: pick the 'Ultra Suite' profile in deck Studio"
}

# 5. Start at sign-in -----------------------------------------------------------
Step "Turning on start-at-sign-in"
if ($NoAutostart) { Warn "Skipped (-NoAutostart)" } else {
    $startup = [Environment]::GetFolderPath("Startup")
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path $startup "Ultra Suite.lnk"))
    $lnk.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnk.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Root\start_all_windows.ps1`" -NoBrowser -Quiet"
    $lnk.WorkingDirectory = $Root; $lnk.WindowStyle = 7; $lnk.Description = "Starts the Ultra Productivity Suite"; $lnk.Save()
    Ok "Suite starts when you sign in"
    if ($lhmExe -and -not (Get-ScheduledTask -TaskName "UltraSuite LibreHardwareMonitor" -ErrorAction SilentlyContinue)) {
        Warn "Windows will ask for admin approval once, so hardware sensors can start at sign-in without prompts."
        $user = "$env:USERDOMAIN\$env:USERNAME"
        $cmd = "`$a=New-ScheduledTaskAction -Execute '$lhmExe'; `$t=New-ScheduledTaskTrigger -AtLogOn -User '$user'; " +
               "`$p=New-ScheduledTaskPrincipal -UserId '$user' -LogonType Interactive -RunLevel Highest; " +
               "`$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0; " +
               "Register-ScheduledTask -TaskName 'UltraSuite LibreHardwareMonitor' -Action `$a -Trigger `$t -Principal `$p -Settings `$s -Force | Out-Null"
        try {
            Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command", $cmd
            if (Get-ScheduledTask -TaskName "UltraSuite LibreHardwareMonitor" -ErrorAction SilentlyContinue) { Ok "Sensors start at sign-in" }
        } catch { Warn "Admin approval was declined; sensors will ask for approval when they start." }
    }
}

# 6. Start ----------------------------------------------------------------------
Step "Starting the suite"
if ($FromLauncher -or $NoStart) { Ok "Setup complete." } else {
    & "$Root\start_all_windows.ps1"
    Write-Host "  Setup complete. Everything will now start on its own when you sign in." -ForegroundColor Green
    Write-Host ""
}
