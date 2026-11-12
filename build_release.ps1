#Requires -Version 5.1
<#
.SYNOPSIS
  Builds the Ultra Suite Windows installer: a PyInstaller core plus a Tauri/NSIS installer.
  -Edition Public (default): build\dist\ultrasuite-core and
      ultra_dock\src-tauri\target\release\bundle\nsis\Ultra Suite_<version>_x64-setup.exe (the GitHub release).
  -Edition Dev: build\editions\Dev\dist\ultrasuite-dev-core and
      build\editions\Dev\release\UltraSuite-Dev-Setup-<version>.exe, an owner-only local installer for
      "Ultra Suite (Dev)". It installs next to the consumer edition with its own folder, data, ports, app ids,
      shortcuts, Run value and uninstall entry, monochrome icons, no alarm delivery, no GitHub updates, no telemetry.
      Never upload it.
  Needs: Python 3.11+ with requirements.txt and the exact pins in requirements-build.txt, Rust (MSVC),
  VS Build Tools (C++), Node.js. The Tauri CLI version comes from ultra_dock\package-lock.json (npm ci).
  Refuses uncommitted tracked changes unless -AllowDirty; <output>\build-environment.json records the inputs.
  Every native step must exit 0 or the build stops; the installer reported at the end is verified to be new.
#>
param([switch]$AllowDirty, [ValidateSet("Public", "Dev")][string]$Edition = "Public")
$ErrorActionPreference = "Stop"
$R = $PSScriptRoot
$helpers = Join-Path $R "tools\build_helpers.ps1"
if (-not (Test-Path -LiteralPath $helpers -PathType Leaf)) {
    throw "Missing $helpers. The build needs it (native exit-code checks, safe cleanup, fresh-artifact checks)."
}
. $helpers
$Dev = $Edition -eq "Dev"
# Keep the caller's prepared Python and PATH, then discover newly installed tools.
$PythonExe = Initialize-BuildTools
Write-Host "Build Python: $PythonExe"
Invoke-Native -Step "Python version" -FilePath $PythonExe -ArgumentList @("-VV")
Set-Location $R
# Building never touches the installed apps: they stay running (alarms keep ringing) while artifacts are made in
# build\ and the Cargo target folders. Installing is a separate, alarm-gated step.
if (-not (Test-Path -LiteralPath "$R\build")) {
    New-Item -ItemType Directory -Path "$R\build" | Out-Null
}
$Out = if ($Dev) { "$R\build\editions\Dev" } else { "$R\build" }
$CoreName = if ($Dev) { "ultrasuite-dev-core" } else { "ultrasuite-core" }
if (-not (Test-Path -LiteralPath $Out)) { New-Item -ItemType Directory -Path $Out | Out-Null }
Remove-BuildOutput -Target "$Out\dist\$CoreName" -BuildRoot "$R\build"
# Exact build-tool versions in the prepared interpreter; record commit, interpreter, packages and tool versions.
$envArgs = @("-I", "$R\tools\check_build_env.py", "--repo", $R, "--requirements", "$R\requirements-build.txt",
    "--record", "$Out\build-environment.json")
if ($AllowDirty) { $envArgs += "--allow-dirty" }
Invoke-Native -Step "build environment" -FilePath $PythonExe -ArgumentList $envArgs
# A Dev build must not change any tracked file (the Public sources stay exactly as committed).
$trackedBefore = if ($Dev) { (& git -C $R status --porcelain --untracked-files=no) -join "`n" } else { "" }

$Version = (Get-Content "$R\VERSION" -Raw).Trim()
Write-Host "Ultra Suite $Version ($Edition edition)" -ForegroundColor Cyan

Write-Host "[1/3] Generating app icons" -ForegroundColor Cyan
Invoke-Native -Step "app icons" -FilePath $PythonExe -ArgumentList @("-I", "$R\ultra_dock\tools\make_icons.py", "--edition", $Edition)

# keep the desktop app's version in step with VERSION (no change when it already matches)
$conf = "$R\ultra_dock\src-tauri\tauri.conf.json"
$c = [IO.File]::ReadAllText($conf) -replace '"version":\s*"[^"]+"', ('"version": "' + $Version + '"')
[IO.File]::WriteAllText($conf, $c, (New-Object Text.UTF8Encoding($false)))
$cargo = "$R\ultra_dock\src-tauri\Cargo.toml"
$t = [IO.File]::ReadAllText($cargo) -replace '(?m)^version = "[^"]+"', ('version = "' + $Version + '"')
[IO.File]::WriteAllText($cargo, $t, (New-Object Text.UTF8Encoding($false)))

Write-Host "[2/3] Building $CoreName.exe" -ForegroundColor Cyan
# Package only the files listed in tools\package_manifest.json, copied into a checked stage with a SHA-256 inventory.
# PyInstaller reads the stage, never the working folders, so local logs, backups, caches and data cannot ship.
# The core picks its edition from its own file name, so the same sources build both editions.
$Stage = "$Out\stage"
$StageTool = "$R\tools\package_stage.py"
Invoke-Native -Step "package stage" -FilePath $PythonExe -ArgumentList @("-I", $StageTool, "--repo", $R,
    "--manifest", "$R\tools\package_manifest.json", "--stage", $Stage)
$cs = "fastapi starlette anyio h11 httpcore httpx pydantic pydantic_core sniffio uvicorn websockets pycaw comtypes idna certifi" -split " " | ForEach-Object { "--collect-submodules"; $_ }
$coreArgs = @("-m", "PyInstaller", "--noconfirm", "--clean", "--noconsole", "--log-level", "WARN", "--name", $CoreName,
    "--distpath", "$Out\dist", "--workpath", "$Out\work", "--specpath", $Out,
    "--add-data", "$Stage\payload;.",
    "--collect-all", "_sounddevice_data", "--collect-data", "certifi", "--hidden-import", "sounddevice", "--hidden-import", "psutil") +
    $cs + @("--hidden-import", "xml.etree.ElementTree", "--hidden-import", "importlib.util", "--hidden-import", "tempfile",
    "$R\core\suite_core.py")
$coreStarted = [DateTime]::UtcNow
Invoke-Native -Step "PyInstaller core" -FilePath $PythonExe -ArgumentList $coreArgs
$null = Assert-FreshFile -Path "$Out\dist\$CoreName\$CoreName.exe" -NotBeforeUtc $coreStarted -What "The background service ($CoreName.exe)"
# The packaged app folders must hold exactly the staged files, byte for byte (names only are reported on failure).
Invoke-Native -Step "packaged file check" -FilePath $PythonExe -ArgumentList @("-I", $StageTool, "--stage", $Stage,
    "--verify-output", "$Out\dist\$CoreName\_internal")

Write-Host "[3/3] Building the installer" -ForegroundColor Cyan
Set-Location "$R\ultra_dock"
$installerStarted = [DateTime]::UtcNow
# The Tauri CLI version and its download integrity come from the tracked package-lock.json; install scripts stay off.
Invoke-Native -Step "Tauri CLI (npm ci)" -FilePath "npm.cmd" -ArgumentList @("ci", "--ignore-scripts", "--no-audit", "--no-fund")
if (-not $Dev) {
    Invoke-Native -Step "Tauri installer" -FilePath "$R\ultra_dock\node_modules\.bin\tauri.cmd" -ArgumentList @("build")
    $installer = Assert-FreshFile -Path "$R\ultra_dock\src-tauri\target\release\bundle\nsis\Ultra Suite_${Version}_x64-setup.exe" -NotBeforeUtc $installerStarted -What "The $Version installer"
    $installer | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}}, @{n="Built (UTC)";e={$_.LastWriteTimeUtc.ToString("u")}}
    return
}

# Dev: a merged Tauri config gives the Dev identity, Dev icons, Dev hooks and the Dev core, in its own Cargo target
# folder (the Public build output is never touched). Paths are relative to ultra_dock\src-tauri.
$devConf = [ordered]@{
    productName = "Ultra Suite (Dev)"
    mainBinaryName = "Ultra Suite Dev"
    identifier = "com.ultrasuite.dev"
    bundle = [ordered]@{
        shortDescription = "Ultra Suite (Dev): the owner's development edition"
        icon = @("icons/dev/32x32.png", "icons/dev/128x128.png", "icons/dev/icon.ico")
        resources = [ordered]@{
            "../../build/dist/ultrasuite-core/" = $null
            "appicons/" = $null
            "../../build/editions/Dev/dist/ultrasuite-dev-core/" = "core/"
            "appicons-dev/" = "appicons/"
        }
        windows = @{ nsis = [ordered]@{ installerIcon = "icons/dev/icon.ico"; installerHooks = "./installer-hooks-dev.nsh" } }
    }
}
$devConfPath = "$Out\tauri.dev.conf.json"
[IO.File]::WriteAllText($devConfPath, ($devConf | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
$devTarget = "$Out\target"
$previousTarget = $env:CARGO_TARGET_DIR
try {
    $env:CARGO_TARGET_DIR = $devTarget
    Invoke-Native -Step "Tauri Dev installer" -FilePath "$R\ultra_dock\node_modules\.bin\tauri.cmd" -ArgumentList @("build", "--config", $devConfPath, "--features", "dev-edition")
} finally {
    $env:CARGO_TARGET_DIR = $previousTarget
}
# The bundle must hold the Dev core only (never the consumer core, which would make it look like the Public edition).
$bundledCore = "$devTarget\release\core"
if (-not (Test-Path -LiteralPath "$bundledCore\$CoreName.exe")) { throw "The Dev bundle has no $CoreName.exe in $bundledCore." }
if (Test-Path -LiteralPath "$bundledCore\ultrasuite-core.exe") { throw "The Dev bundle also contains the consumer core; refusing to use it." }
$built = Assert-FreshFile -Path "$devTarget\release\bundle\nsis\Ultra Suite (Dev)_${Version}_x64-setup.exe" -NotBeforeUtc $installerStarted -What "The Dev $Version installer"
$release = "$Out\release"
if (-not (Test-Path -LiteralPath $release)) { New-Item -ItemType Directory -Path $release | Out-Null }
$final = "$release\UltraSuite-Dev-Setup-$Version.exe"
Copy-Item -LiteralPath $built.FullName -Destination $final -Force
$hash = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne (Get-FileHash -LiteralPath $built.FullName -Algorithm SHA256).Hash.ToLowerInvariant()) { throw "The copied Dev installer does not match the built one." }
$trackedAfter = (& git -C $R status --porcelain --untracked-files=no) -join "`n"
if ($trackedAfter -ne $trackedBefore) { throw "The Dev build changed tracked files:`n$trackedAfter" }
Get-Item -LiteralPath $final | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}}, @{n="Built (UTC)";e={$_.LastWriteTimeUtc.ToString("u")}}
Write-Host "SHA-256 $hash"
