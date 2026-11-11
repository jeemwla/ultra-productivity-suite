#Requires -Version 5.1
<#
.SYNOPSIS
  Builds the Ultra Suite Windows installer: build\dist\ultrasuite-core (PyInstaller) + Ultra Suite_x64-setup.exe (Tauri/NSIS).
  Needs: Python 3.11+ with requirements.txt and pyinstaller, Rust (MSVC), VS Build Tools (C++), Node.js.
  Output: 02_translucent_taskbar_dock\src-tauri\target\release\bundle\nsis\Ultra Suite_<version>_x64-setup.exe
#>
$ErrorActionPreference = "Stop"
$R = $PSScriptRoot
Set-Location $R
Get-Process "Ultra Suite", ultrasuite-core -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item "$R\build\dist\ultrasuite-core" -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "[1/3] Building ultrasuite-core.exe" -ForegroundColor Cyan
$cs = "fastapi starlette anyio h11 httpcore httpx pydantic pydantic_core sniffio uvicorn websockets pycaw comtypes idna certifi" -split " " | ForEach-Object { "--collect-submodules"; $_ }
python -m PyInstaller --noconfirm --clean --noconsole --log-level WARN --name ultrasuite-core `
  --distpath "$R\build\dist" --workpath "$R\build\work" --specpath "$R\build" `
  --add-data "$R\01_pebble_code_red_alarm\backend;01_pebble_code_red_alarm\backend" `
  --add-data "$R\01_pebble_code_red_alarm\frontend;01_pebble_code_red_alarm\frontend" `
  --add-data "$R\04_hardware_net_sentinel\backend;04_hardware_net_sentinel\backend" `
  --add-data "$R\04_hardware_net_sentinel\frontend;04_hardware_net_sentinel\frontend" `
  --add-data "$R\03_deck_deck_config\generate_deck_profile.py;03_deck_deck_config" `
  --add-data "$R\03_deck_deck_config\plugin\volume_knob_daemon.py;03_deck_deck_config\plugin" `
  --add-data "$R\03_deck_deck_config\com.ultrasuite.knobs.deckPlugin;03_deck_deck_config\com.ultrasuite.knobs.deckPlugin" `
  --collect-all _sounddevice_data --collect-data certifi --hidden-import sounddevice --hidden-import psutil @cs `
  "$R\core\suite_core.py"

Write-Host "[2/3] Generating app icons" -ForegroundColor Cyan
python -I "$R\02_translucent_taskbar_dock\tools\make_icons.py"

Write-Host "[3/3] Building the installer" -ForegroundColor Cyan
Set-Location "$R\02_translucent_taskbar_dock"
npx -y @tauri-apps/cli@2 build
Get-ChildItem "$R\02_translucent_taskbar_dock\src-tauri\target\release\bundle\nsis\*.exe" | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}}
