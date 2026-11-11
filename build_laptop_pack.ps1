#Requires -Version 5.1
<#
.SYNOPSIS
  Builds the Laptop Pack add-on installer: build\dist\UltraSuite-LaptopPack-<version>-x64.exe
  Needs NSIS (makensis). Tauri's build downloads it to %LOCALAPPDATA%\tauri\NSIS.
#>
$ErrorActionPreference = "Stop"
$R = $PSScriptRoot
$ver = (Select-String -Path "$R\06_laptop_pack\laptop_pack.py" -Pattern 'PACK_VERSION = "([^"]+)"').Matches[0].Groups[1].Value
$nsis = @("$env:LOCALAPPDATA\tauri\NSIS\makensis.exe", "${env:ProgramFiles(x86)}\NSIS\makensis.exe", "$env:ProgramFiles\NSIS\makensis.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $nsis) { throw "makensis.exe not found. Run build_release.ps1 once (Tauri downloads NSIS) or install NSIS." }
if (-not (Test-Path "$R\02_translucent_taskbar_dock\src-tauri\appicons\sentinel.ico")) { python -I "$R\02_translucent_taskbar_dock\tools\make_icons.py" }
New-Item -ItemType Directory -Force "$R\build\dist" | Out-Null
& $nsis /V2 "/DVERSION=$ver" "$R\06_laptop_pack\installer.nsi"
if ($LASTEXITCODE -ne 0) { throw "makensis failed" }
Get-Item "$R\build\dist\UltraSuite-LaptopPack-$ver-x64.exe" | Select-Object Name, @{n = "KB"; e = { [math]::Round($_.Length / 1KB) } }
