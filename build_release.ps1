#Requires -Version 5.1
<#
.SYNOPSIS
  Builds the Ultra Suite Windows installer: build\dist\ultrasuite-core (PyInstaller) + Ultra Suite_x64-setup.exe (Tauri/NSIS).
  Needs: Python 3.11+ with requirements.txt and pyinstaller, Rust (MSVC), VS Build Tools (C++), Node.js.
  Output: ultra_dock\src-tauri\target\release\bundle\nsis\Ultra Suite_<version>_x64-setup.exe
#>
$ErrorActionPreference = "Stop"
$R = $PSScriptRoot
# pick up tools installed since this window opened (Node, Rust)
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:USERPROFILE\.cargo\bin"
Set-Location $R
# Building never touches the installed app: it stays running (alarms keep ringing) while artifacts are made in
# build\ and ultra_dock\src-tauri\target\. Installing is a separate step (build\install_outside.cmd).
Remove-Item "$R\build\dist\ultrasuite-core" -Recurse -Force -ErrorAction SilentlyContinue

$Version = (Get-Content "$R\VERSION" -Raw).Trim()
Write-Host "Ultra Suite $Version" -ForegroundColor Cyan

Write-Host "[1/3] Generating app icons" -ForegroundColor Cyan
python -I "$R\ultra_dock\tools\make_icons.py"

# keep the desktop app's version in step with VERSION
$conf = "$R\ultra_dock\src-tauri\tauri.conf.json"
$c = [IO.File]::ReadAllText($conf) -replace '"version":\s*"[^"]+"', ('"version": "' + $Version + '"')
[IO.File]::WriteAllText($conf, $c, (New-Object Text.UTF8Encoding($false)))
$cargo = "$R\ultra_dock\src-tauri\Cargo.toml"
$t = [IO.File]::ReadAllText($cargo) -replace '(?m)^version = "[^"]+"', ('version = "' + $Version + '"')
[IO.File]::WriteAllText($cargo, $t, (New-Object Text.UTF8Encoding($false)))

Write-Host "[2/3] Building ultrasuite-core.exe" -ForegroundColor Cyan
$cs = "fastapi starlette anyio h11 httpcore httpx pydantic pydantic_core sniffio uvicorn websockets pycaw comtypes idna certifi" -split " " | ForEach-Object { "--collect-submodules"; $_ }
python -m PyInstaller --noconfirm --clean --noconsole --log-level WARN --name ultrasuite-core `
  --distpath "$R\build\dist" --workpath "$R\build\work" --specpath "$R\build" `
  --add-data "$R\code_red_alarm\backend;code_red_alarm\backend" `
  --add-data "$R\code_red_alarm\frontend;code_red_alarm\frontend" `
  --add-data "$R\pc_sentinel\backend;pc_sentinel\backend" `
  --add-data "$R\pc_sentinel\frontend;pc_sentinel\frontend" `
  --add-data "$R\insights;insights" --add-data "$R\core\feedback.py;core" `
  --add-data "$R\core\devtools.py;core" --add-data "$R\core\static;core\static" --add-data "$R\VERSION;." `
  --collect-all _sounddevice_data --collect-data certifi --hidden-import sounddevice --hidden-import psutil @cs `
  --hidden-import xml.etree.ElementTree --hidden-import importlib.util --hidden-import tempfile `
  "$R\core\suite_core.py"

Write-Host "[3/3] Building the installer" -ForegroundColor Cyan
Set-Location "$R\ultra_dock"
npx -y @tauri-apps/cli@2 build
Get-ChildItem "$R\ultra_dock\src-tauri\target\release\bundle\nsis\*.exe" | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}}

