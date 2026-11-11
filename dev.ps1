#Requires -Version 5.1
<#
.SYNOPSIS
  Owner dev mode: runs every Ultra Suite app from this folder with live reload.
  - Python services restart automatically when a .py file changes (watchfiles)
  - Pages reload by themselves when their HTML/CSS/JS changes (red "DEV" badge)
  - The taskbar organizer runs from source (Rust changes: run this script again)
  Data for dev mode lives in .devdata\ so it never touches the installed app's data.
  Back to normal: stop_dev.ps1
#>
param([switch]$NoDock, [switch]$NoBrowser)
$ErrorActionPreference = "Continue"
$R = $PSScriptRoot
Set-Location $R
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:USERPROFILE\.cargo\bin"

Write-Host ""; Write-Host "  Ultra Suite dev mode" -ForegroundColor Cyan

# 1. quit the installed app cleanly (restores taskbar buttons), then free the ports
$installed = "$env:LOCALAPPDATA\Ultra Suite\Ultra Suite.exe"
foreach ($exe in @($installed, "$R\02_translucent_taskbar_dock\src-tauri\target\release\ultra-suite.exe", "$R\02_translucent_taskbar_dock\src-tauri\target\debug\ultra-suite.exe")) {
    if ((Test-Path $exe) -and (Get-Process | Where-Object { $_.Path -eq $exe })) { Start-Process $exe -ArgumentList "--quit"; Start-Sleep 2 }
}
foreach ($port in 8081, 8084, 8091, 8093, 8300, 8301) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}

# 2. services from source with auto-restart
New-Item -ItemType Directory -Force "$R\.devdata", "$R\logs" | Out-Null
$env:ULTRASUITE_DEV = "1"; $env:ULTRASUITE_OWNER = "1"; $env:ULTRASUITE_DATA = "$R\.devdata"
$watch = @("$R\core", "$R\01_pebble_code_red_alarm\backend", "$R\04_hardware_net_sentinel\backend", "$R\05_insights", "$R\03_deck_deck_config\plugin", "$R\addons")
$watch = $watch | Where-Object { Test-Path $_ }
Start-Process python -ArgumentList (@("-m", "watchfiles", "--filter", "python", "--target-type", "command", "`"python -I $R\core\suite_core.py`"") + ($watch | ForEach-Object { "`"$_`"" })) `
    -WorkingDirectory $R -WindowStyle Hidden -RedirectStandardOutput "$R\logs\dev_core.log" -RedirectStandardError "$R\logs\dev_core.err.log"
$ok = $false; for ($i = 0; $i -lt 40; $i++) { Start-Sleep -Milliseconds 500; if (Get-NetTCPConnection -LocalPort 8084 -State Listen -ErrorAction SilentlyContinue) { $ok = $true; break } }
if ($ok) { Write-Host "  [OK] services running from source (auto-restart on save)" -ForegroundColor Green } else { Write-Host "  [!] services didn't start; see logs\dev_core.err.log" -ForegroundColor Yellow }

# 3. taskbar organizer from source
if (-not $NoDock) {
    Write-Host "  building the taskbar organizer (first build takes about a minute)..." -ForegroundColor DarkCyan
    Push-Location "$R\02_translucent_taskbar_dock\src-tauri"; cargo build *> "$R\logs\dev_dock_build.log"; Pop-Location
    $dbg = "$R\02_translucent_taskbar_dock\src-tauri\target\debug\ultra-suite.exe"
    if (Test-Path $dbg) { Start-Process $dbg -ArgumentList "--background"; Write-Host "  [OK] organizer running (debug build)" -ForegroundColor Green }
    else { Write-Host "  [!] organizer build failed; see logs\dev_dock_build.log" -ForegroundColor Yellow }
}
if (-not $NoBrowser) { Start-Process "http://localhost:8084"; Start-Process "http://localhost:8081" }
Write-Host "  Dev pages: http://localhost:8081  http://localhost:8084  http://localhost:8301 (Insights)" -ForegroundColor Cyan
Write-Host ""
