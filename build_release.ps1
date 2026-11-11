#Requires -Version 5.1
<#
.SYNOPSIS
  Builds the Ultra Suite Windows installer: build\dist\ultrasuite-core (PyInstaller) + Ultra Suite_x64-setup.exe (Tauri/NSIS).
  Needs: Python 3.11+ with requirements.txt and the exact pins in requirements-build.txt, Rust (MSVC),
  VS Build Tools (C++), Node.js. The Tauri CLI version comes from ultra_dock\package-lock.json (npm ci).
  Refuses uncommitted tracked changes unless -AllowDirty; build\build-environment.json records the inputs.
  Output: ultra_dock\src-tauri\target\release\bundle\nsis\Ultra Suite_<version>_x64-setup.exe
  Every native step must exit 0 or the build stops; the installer reported at the end is verified to be new.
#>
param([switch]$AllowDirty)
$ErrorActionPreference = "Stop"
$R = $PSScriptRoot
$helpers = Join-Path $R "tools\build_helpers.ps1"
if (-not (Test-Path -LiteralPath $helpers -PathType Leaf)) {
    throw "Missing $helpers. The build needs it (native exit-code checks, safe cleanup, fresh-artifact checks)."
}
. $helpers
# Keep the caller's prepared Python and PATH, then discover newly installed tools.
$PythonExe = Initialize-BuildTools
Write-Host "Build Python: $PythonExe"
Invoke-Native -Step "Python version" -FilePath $PythonExe -ArgumentList @("-VV")
Set-Location $R
# Building never touches the installed app: it stays running (alarms keep ringing) while artifacts are made in
# build\ and ultra_dock\src-tauri\target\. Installing is a separate step (build\install_outside.cmd).
if (-not (Test-Path -LiteralPath "$R\build")) {
    New-Item -ItemType Directory -Path "$R\build" | Out-Null
}
Remove-BuildOutput -Target "$R\build\dist\ultrasuite-core" -BuildRoot "$R\build"
# Exact build-tool versions in the prepared interpreter; record commit, interpreter, packages and tool versions.
$envArgs = @("-I", "$R\tools\check_build_env.py", "--repo", $R, "--requirements", "$R\requirements-build.txt",
    "--record", "$R\build\build-environment.json")
if ($AllowDirty) { $envArgs += "--allow-dirty" }
Invoke-Native -Step "build environment" -FilePath $PythonExe -ArgumentList $envArgs

$Version = (Get-Content "$R\VERSION" -Raw).Trim()
Write-Host "Ultra Suite $Version" -ForegroundColor Cyan

Write-Host "[1/3] Generating app icons" -ForegroundColor Cyan
Invoke-Native -Step "app icons" -FilePath $PythonExe -ArgumentList @("-I", "$R\ultra_dock\tools\make_icons.py")

# keep the desktop app's version in step with VERSION
$conf = "$R\ultra_dock\src-tauri\tauri.conf.json"
$c = [IO.File]::ReadAllText($conf) -replace '"version":\s*"[^"]+"', ('"version": "' + $Version + '"')
[IO.File]::WriteAllText($conf, $c, (New-Object Text.UTF8Encoding($false)))
$cargo = "$R\ultra_dock\src-tauri\Cargo.toml"
$t = [IO.File]::ReadAllText($cargo) -replace '(?m)^version = "[^"]+"', ('version = "' + $Version + '"')
[IO.File]::WriteAllText($cargo, $t, (New-Object Text.UTF8Encoding($false)))

Write-Host "[2/3] Building ultrasuite-core.exe" -ForegroundColor Cyan
# Package only the files listed in tools\package_manifest.json, copied into a checked stage with a SHA-256 inventory.
# PyInstaller reads the stage, never the working folders, so local logs, backups, caches and data cannot ship.
$Stage = "$R\build\stage"
$StageTool = "$R\tools\package_stage.py"
Invoke-Native -Step "package stage" -FilePath $PythonExe -ArgumentList @("-I", $StageTool, "--repo", $R,
    "--manifest", "$R\tools\package_manifest.json", "--stage", $Stage)
$cs = "fastapi starlette anyio h11 httpcore httpx pydantic pydantic_core sniffio uvicorn websockets pycaw comtypes idna certifi" -split " " | ForEach-Object { "--collect-submodules"; $_ }
$coreArgs = @("-m", "PyInstaller", "--noconfirm", "--clean", "--noconsole", "--log-level", "WARN", "--name", "ultrasuite-core",
    "--distpath", "$R\build\dist", "--workpath", "$R\build\work", "--specpath", "$R\build",
    "--add-data", "$Stage\payload;.",
    "--collect-all", "_sounddevice_data", "--collect-data", "certifi", "--hidden-import", "sounddevice", "--hidden-import", "psutil") +
    $cs + @("--hidden-import", "xml.etree.ElementTree", "--hidden-import", "importlib.util", "--hidden-import", "tempfile",
    "$R\core\suite_core.py")
$coreStarted = [DateTime]::UtcNow
Invoke-Native -Step "PyInstaller core" -FilePath $PythonExe -ArgumentList $coreArgs
$null = Assert-FreshFile -Path "$R\build\dist\ultrasuite-core\ultrasuite-core.exe" -NotBeforeUtc $coreStarted -What "The background service (ultrasuite-core.exe)"
# The packaged app folders must hold exactly the staged files, byte for byte (names only are reported on failure).
Invoke-Native -Step "packaged file check" -FilePath $PythonExe -ArgumentList @("-I", $StageTool, "--stage", $Stage,
    "--verify-output", "$R\build\dist\ultrasuite-core\_internal")

Write-Host "[3/3] Building the installer" -ForegroundColor Cyan
Set-Location "$R\ultra_dock"
$installerStarted = [DateTime]::UtcNow
# The Tauri CLI version and its download integrity come from the tracked package-lock.json; install scripts stay off.
Invoke-Native -Step "Tauri CLI (npm ci)" -FilePath "npm.cmd" -ArgumentList @("ci", "--ignore-scripts", "--no-audit", "--no-fund")
Invoke-Native -Step "Tauri installer" -FilePath "$R\ultra_dock\node_modules\.bin\tauri.cmd" -ArgumentList @("build")
$installer = Assert-FreshFile -Path "$R\ultra_dock\src-tauri\target\release\bundle\nsis\Ultra Suite_${Version}_x64-setup.exe" -NotBeforeUtc $installerStarted -What "The $Version installer"
$installer | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}}, @{n="Built (UTC)";e={$_.LastWriteTimeUtc.ToString("u")}}
