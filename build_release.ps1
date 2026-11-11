#Requires -Version 5.1
<#
.SYNOPSIS
  Builds the Ultra Suite Windows installer: build\dist\ultrasuite-core (PyInstaller) + Ultra Suite_x64-setup.exe (Tauri/NSIS).
  Needs: Python 3.11+ with requirements.txt and pyinstaller, Rust (MSVC), VS Build Tools (C++), Node.js.
  Output: 02_translucent_taskbar_dock\src-tauri\target\release\bundle\nsis\Ultra Suite_<version>_x64-setup.exe
#>
$ErrorActionPreference = "Stop"
$R = $PSScriptRoot
# pick up tools installed since this window opened (Node, Rust)
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:USERPROFILE\.cargo\bin"
Set-Location $R
# quit the installed app cleanly first (a force-kill would leave other apps' taskbar buttons tucked away)
Get-Process "Ultra Suite" -ErrorAction SilentlyContinue | ForEach-Object { Start-Process $_.Path -ArgumentList "--quit"; Start-Sleep 3 }
Get-Process ultrasuite-core -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item "$R\build\dist\ultrasuite-core" -Recurse -Force -ErrorAction SilentlyContinue

$Version = (Get-Content "$R\VERSION" -Raw).Trim()
Write-Host "Ultra Suite $Version" -ForegroundColor Cyan

Write-Host "[1/3] Generating app icons" -ForegroundColor Cyan
python -I "$R\02_translucent_taskbar_dock\tools\make_icons.py"

# keep the desktop app's version in step with VERSION
$conf = "$R\02_translucent_taskbar_dock\src-tauri\tauri.conf.json"
$c = [IO.File]::ReadAllText($conf) -replace '"version":\s*"[^"]+"', ('"version": "' + $Version + '"')
[IO.File]::WriteAllText($conf, $c, (New-Object Text.UTF8Encoding($false)))
$cargo = "$R\02_translucent_taskbar_dock\src-tauri\Cargo.toml"
$t = [IO.File]::ReadAllText($cargo) -replace '(?m)^version = "[^"]+"', ('version = "' + $Version + '"')
[IO.File]::WriteAllText($cargo, $t, (New-Object Text.UTF8Encoding($false)))

Write-Host "[2/3] Building ultrasuite-core.exe" -ForegroundColor Cyan
$cs = "fastapi starlette anyio h11 httpcore httpx pydantic pydantic_core sniffio uvicorn websockets pycaw comtypes idna certifi" -split " " | ForEach-Object { "--collect-submodules"; $_ }
python -m PyInstaller --noconfirm --clean --noconsole --log-level WARN --name ultrasuite-core `
  --distpath "$R\build\dist" --workpath "$R\build\work" --specpath "$R\build" `
  --add-data "$R\01_pebble_code_red_alarm\backend;01_pebble_code_red_alarm\backend" `
  --add-data "$R\01_pebble_code_red_alarm\frontend;01_pebble_code_red_alarm\frontend" `
  --add-data "$R\04_hardware_net_sentinel\backend;04_hardware_net_sentinel\backend" `
  --add-data "$R\04_hardware_net_sentinel\frontend;04_hardware_net_sentinel\frontend" `
  --add-data "$R\03_deck_deck_config\generate_deck_profile.py;03_deck_deck_config" `
  --add-data "$R\05_insights;05_insights" --add-data "$R\core\feedback.py;core" `
  --add-data "$R\core\devtools.py;core" --add-data "$R\core\static;core\static" --add-data "$R\VERSION;." `
  --add-data "$R\03_deck_deck_config\plugin\volume_knob_daemon.py;03_deck_deck_config\plugin" `
  --add-data "$R\03_deck_deck_config\com.ultrasuite.knobs.deckPlugin;03_deck_deck_config\com.ultrasuite.knobs.deckPlugin" `
  --collect-all _sounddevice_data --collect-data certifi --hidden-import sounddevice --hidden-import psutil @cs `
  "$R\core\suite_core.py"

Write-Host "[3/3] Building the installer" -ForegroundColor Cyan
Set-Location "$R\02_translucent_taskbar_dock"
npx -y @tauri-apps/cli@2 build
Get-ChildItem "$R\02_translucent_taskbar_dock\src-tauri\target\release\bundle\nsis\*.exe" | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}}

