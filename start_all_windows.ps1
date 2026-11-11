#Requires -Version 5.1
<#
.SYNOPSIS
  Starts the Ultra Productivity Suite on this PC.
.DESCRIPTION
  - Host audio bridge (speaker picker + volume lock + OneScript hook)  127.0.0.1:8091
  - deck knob daemon                                                 127.0.0.1:8093
  - LibreHardwareMonitor web server                                    127.0.0.1:8085
  - Code Red Alarm web app                                             127.0.0.1:8081
  - PC Sentinel web app                                                127.0.0.1:8084
  - Ultra Dock (taskbar dock), if installed
  - Tailscale Serve for your phone (tailnet only), if Tailscale is installed
  Safe to run again: anything already running is left alone.
.PARAMETER NoBrowser   Don't open the dashboards.
.PARAMETER Quiet       No console output (used at sign-in).
.PARAMETER Docker      Run the two web apps in Docker instead of natively.
#>
param([switch]$NoBrowser, [switch]$Quiet, [switch]$Docker)
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
$Root = $PSScriptRoot
$Logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force $Logs | Out-Null

function Say([string]$msg, [string]$color = "Gray") { if (-not $Quiet) { Write-Host $msg -ForegroundColor $color } }
function Test-Port([int]$Port) {
    try { $c = New-Object Net.Sockets.TcpClient; $iar = $c.BeginConnect("127.0.0.1", $Port, $null, $null)
          $ok = $iar.AsyncWaitHandle.WaitOne(300); if ($ok) { $c.EndConnect($iar) }; $c.Close(); return $ok } catch { return $false }
}
function Wait-Port([int]$Port, [int]$Seconds = 20) {
    $end = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $end) { if (Test-Port $Port) { return $true }; Start-Sleep -Milliseconds 400 }
    return $false
}

Say ""
Say "  Ultra Productivity Suite" "Cyan"
Say "  ------------------------" "DarkCyan"

# --- .env -> this process (child processes inherit it)
if (-not (Test-Path ".env")) {
    if (Test-Path "setup.ps1") { Say "  First run: running setup.ps1 ..." "Yellow"; & "$Root\setup.ps1" -FromLauncher; }
    if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
}
Get-Content ".env" | Where-Object { $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), "Process")
}
if ($env:SUITE_MODE -eq "docker") { $Docker = $true }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $py) { Say "  [X] Python not found. Run setup.ps1 first." "Red"; exit 1 }

function Start-Hidden([string]$Name, [string[]]$PyArgs, [string]$WorkDir = $Root) {
    $out = Join-Path $Logs "$Name.log"; $err = Join-Path $Logs "$Name.err.log"
    Start-Process -FilePath $py -ArgumentList $PyArgs -WorkingDirectory $WorkDir -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null
}

$status = [ordered]@{}

# 1. Host bridge + knob daemon
if (-not (Test-Port 8091)) { Start-Hidden "audio_bridge" @("-I", "`"$Root\01_pebble_code_red_alarm\backend\audio_enforcer.py`"", "--serve") }
if (-not (Test-Port 8093)) { Start-Hidden "knob_daemon" @("-I", "`"$Root\03_deck_deck_config\plugin\volume_knob_daemon.py`"") }

# 2. LibreHardwareMonitor (prefer the elevated sign-in task, so no UAC prompt)
if (-not (Test-Port 8085)) {
    $task = Get-ScheduledTask -TaskName "UltraSuite LibreHardwareMonitor" -ErrorAction SilentlyContinue
    if ($task) { Start-ScheduledTask -TaskName "UltraSuite LibreHardwareMonitor" }
    elseif (-not (Get-Process LibreHardwareMonitor -ErrorAction SilentlyContinue)) {
        $lhm = $env:LHM_EXE
        if (-not $lhm -or -not (Test-Path $lhm)) { $lhm = (Get-Command LibreHardwareMonitor -ErrorAction SilentlyContinue).Source }
        if ($lhm) { try { Start-Process $lhm -ErrorAction Stop } catch { Say "  [!] LibreHardwareMonitor needs admin approval to start." "Yellow" } }
    }
}

# 3. Web apps
if ($Docker) {
    docker info --format "{{.ServerVersion}}" *> $null
    if ($LASTEXITCODE -ne 0) { Say "  [X] Docker Desktop isn't running. Start it, or run without -Docker." "Red" }
    else { docker compose up -d --build *> (Join-Path $Logs "docker.log") }
} else {
    $env:HOST_AUDIO_BRIDGE_URL = "http://127.0.0.1:8091"
    $env:ONESCRIPT_WEBHOOK_URL = "http://127.0.0.1:8091/run-onescript"
    $env:LHM_REMOTE_URL = "http://127.0.0.1:8085/data.json"
    if (-not (Test-Port 8081)) { Start-Hidden "alarm_app" @("-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8081", "--log-level", "warning") "$Root\01_pebble_code_red_alarm" }
    if (-not (Test-Port 8084)) { Start-Hidden "sentinel_app" @("-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8084", "--log-level", "warning") "$Root\04_hardware_net_sentinel" }
}

# 4. Dock
$dock = @("$env:LOCALAPPDATA\UltraSuite\ultra-dock.exe", "$Root\02_translucent_taskbar_dock\src-tauri\target\release\ultra-dock.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($dock -and -not (Get-Process ultra-dock -ErrorAction SilentlyContinue)) { Start-Process $dock }

# 5. Wait and report
$status["Speaker helper  :8091"] = Wait-Port 8091 15
$status["Knob daemon     :8093"] = Wait-Port 8093 10
$status["Code Red Alarm  :8081"] = Wait-Port 8081 30
$status["PC Sentinel     :8084"] = Wait-Port 8084 30
$status["Hardware sensors:8085"] = Wait-Port 8085 20

# 6. Tailscale Serve (tailnet only; never Funnel)
$ts = Get-Command tailscale -ErrorAction SilentlyContinue
if ($ts) {
    $tsJson = tailscale status --json 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($tsJson -and $tsJson.BackendState -eq "Running") {
        tailscale serve --bg --https=8443 http://127.0.0.1:8081 *> $null
        tailscale serve --bg --https=8444 http://127.0.0.1:8084 *> $null
        $dns = ($tsJson.Self.DNSName).TrimEnd('.')
    }
}

Say ""
foreach ($k in $status.Keys) { if ($status[$k]) { Say "  [OK] $k" "Green" } else { Say "  [--] $k  (see logs\ folder)" "Yellow" } }
Say ""
Say "  On this PC:   http://localhost:8081  (alarm)   http://localhost:8084  (sentinel)" "Cyan"
if ($dns) { Say "  On your phone (Tailscale):  https://$($dns):8443   https://$($dns):8444" "Cyan" }
Say ""

if (-not $NoBrowser -and -not $Quiet) {
    if ($status["Code Red Alarm  :8081"]) { Start-Process "http://localhost:8081" }
    if ($status["PC Sentinel     :8084"]) { Start-Process "http://localhost:8084" }
}
