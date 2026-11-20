#Requires -Version 5.1
<#
.SYNOPSIS
  Builds the PC Sentinel sensor helper (UltraSuiteSensorHelper.exe, .NET Framework 4.8) into -OutDir, or with
  -TestOnly compiles and runs its unit tests (no downloads, no LibreHardwareMonitorLib needed).
  Uses the C# compiler that ships with Visual Studio / Build Tools (Roslyn csc.exe) and the .NET Framework 4.8 that
  is part of Windows 10 and 11; no .NET SDK is required. NuGet packages are the exact versions in
  pc_sentinel\sensor_helper\pins.json, downloaded from nuget.org into build\sensor-helper\packages and checked against
  their pinned SHA-512 before anything is extracted. The PawnIO installer is never downloaded or bundled here: the
  installed helper downloads the pinned official release itself, during the elevated setup step.
  Builds never install, start or register anything.
#>
param(
    [string]$OutDir,
    [switch]$TestOnly,
    [string]$Csc
)
$ErrorActionPreference = "Stop"
$R = Split-Path -Parent $PSScriptRoot
$Src = Join-Path $R "pc_sentinel\sensor_helper"
$Pins = Get-Content -LiteralPath (Join-Path $Src "pins.json") -Raw | ConvertFrom-Json
$Fx = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319"
$Work = Join-Path $R "build\sensor-helper"

function Find-Csc {
    if ($Csc) { return $Csc }
    $roots = @("${env:ProgramFiles}\Microsoft Visual Studio", "${env:ProgramFiles(x86)}\Microsoft Visual Studio")
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $found = Get-ChildItem -LiteralPath $root -Recurse -Filter csc.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\MSBuild\\Current\\Bin\\Roslyn\\csc\.exe$' } | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    throw "Roslyn csc.exe was not found. Install Visual Studio Build Tools (C# compiler) or pass -Csc."
}

function Invoke-Csc([string[]]$Arguments, [string]$What) {
    & $script:CscExe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$What failed to compile (csc exit $LASTEXITCODE)." }
}

$script:CscExe = Find-Csc
if (-not (Test-Path -LiteralPath "$Fx\mscorlib.dll")) { throw ".NET Framework 4.x (64-bit) was not found at $Fx." }
$fxRefs = "mscorlib System System.Core System.ServiceProcess System.Management System.Configuration netstandard System.Runtime" -split " " |
    ForEach-Object { "/reference:$Fx\$_.dll" }
$common = @("/nologo", "/noconfig", "/nostdlib+", "/langversion:latest", "/deterministic+", "/optimize+", "/platform:x64",
    "/warn:4", "/warnaserror+", "/nowarn:1701,1702") + $fxRefs
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$version = [string]$Pins.helper_version
$info = Join-Path $Work "AssemblyInfo.g.cs"
$infoText = "[assembly: System.Reflection.AssemblyVersion(`"$version.0`")]`r`n" +
    "[assembly: System.Reflection.AssemblyFileVersion(`"$version.0`")]`r`n" +
    "[assembly: System.Reflection.AssemblyProduct(`"Ultra Suite Sensor Helper`")]`r`n" +
    "[assembly: System.Reflection.AssemblyDescription(`"Read-only hardware sensor reader for PC Sentinel`")]`r`n"
[IO.File]::WriteAllText($info, $infoText, (New-Object Text.UTF8Encoding($false)))

if ($TestOnly) {
    # Everything except the LibreHardwareMonitorLib reader and the entry point, plus the test runner.
    $testExe = Join-Path $Work "HelperTests.exe"
    $files = @("HelperEdition.cs", "Options.cs", "SensorTree.cs", "LoopbackServer.cs", "DriverStatus.cs", "Provisioner.cs") |
        ForEach-Object { Join-Path $Src "src\$_" }
    Invoke-Csc -What "Sensor helper tests" -Arguments ($common + @("/target:exe", "/out:$testExe", $info,
        (Join-Path $Src "tests\HelperTests.cs")) + $files)
    & $testExe
    if ($LASTEXITCODE -ne 0) { throw "Sensor helper tests failed." }
    return
}

if (-not $OutDir) { throw "Pass -OutDir (the folder that becomes core\sensor-helper in the installer) or -TestOnly." }

# Pinned NuGet packages: download once, verify SHA-512, extract only the one library file each needs.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$pkgDir = Join-Path $Work "packages"
$libDir = Join-Path $Work "lib"
New-Item -ItemType Directory -Force -Path $pkgDir, $libDir | Out-Null
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$libs = @()
$licenses = @()
foreach ($p in $Pins.nuget) {
    $id = $p.id.ToLowerInvariant(); $ver = $p.version
    $nupkg = Join-Path $pkgDir "$id.$ver.nupkg"
    if (-not (Test-Path -LiteralPath $nupkg)) {
        $tmp = "$nupkg.download"
        Invoke-WebRequest -UseBasicParsing -Uri "https://api.nuget.org/v3-flatcontainer/$id/$ver/$id.$ver.nupkg" -OutFile $tmp
        Move-Item -LiteralPath $tmp -Destination $nupkg -Force
    }
    $sha = [Security.Cryptography.SHA512]::Create()
    $fs = [IO.File]::OpenRead($nupkg)
    try { $actual = [Convert]::ToBase64String($sha.ComputeHash($fs)) } finally { $fs.Dispose(); $sha.Dispose() }
    if ($actual -ne $p.sha512) {
        Remove-Item -LiteralPath $nupkg -Force
        throw "$($p.id) $ver does not match its pinned SHA-512; the download was deleted."
    }
    $zip = [IO.Compression.ZipFile]::OpenRead($nupkg)
    try {
        # Each package's own license file (if it ships one) is appended to THIRD_PARTY_NOTICES.txt below.
        foreach ($le in ($zip.Entries | Where-Object { $_.FullName -match '^(LICENSE|License|license)[^/]*$' })) {
            $licenses += [pscustomobject]@{ Package = "$($p.id) $ver"; Text = (New-Object IO.StreamReader($le.Open())).ReadToEnd() }
        }
        if (-not $p.lib) { continue } # provided by .NET Framework itself
        $entry = $zip.Entries | Where-Object { $_.FullName -eq $p.lib } | Select-Object -First 1
        if (-not $entry) { throw "$($p.id) $ver has no $($p.lib)." }
        $dest = Join-Path $libDir $entry.Name
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest, $true)
        $libs += $dest
    } finally { $zip.Dispose() }
}

$stage = Join-Path $Work "out"
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
$exe = Join-Path $stage "UltraSuiteSensorHelper.exe"
$sources = Get-ChildItem -LiteralPath (Join-Path $Src "src") -Filter *.cs | ForEach-Object { $_.FullName }
Invoke-Csc -What "Sensor helper" -Arguments ($common + @("/target:exe", "/out:$exe", $info) +
    ($libs | ForEach-Object { "/reference:$_" }) + $sources)
foreach ($l in $libs) { Copy-Item -LiteralPath $l -Destination $stage }

# Binding redirects to the exact library versions shipped next to the exe.
$redirects = foreach ($l in $libs) {
    $n = [Reflection.AssemblyName]::GetAssemblyName($l)
    $token = ($n.GetPublicKeyToken() | ForEach-Object { $_.ToString("x2") }) -join ""
    if (-not $token) { continue }
    "      <dependentAssembly><assemblyIdentity name=`"$($n.Name)`" publicKeyToken=`"$token`" culture=`"neutral`" />" +
        "<bindingRedirect oldVersion=`"0.0.0.0-$($n.Version)`" newVersion=`"$($n.Version)`" /></dependentAssembly>"
}
$config = @"
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8" /></startup>
  <runtime>
    <assemblyBinding xmlns="urn:schemas-microsoft-com:asm.v1">
$($redirects -join "`r`n")
    </assemblyBinding>
  </runtime>
</configuration>
"@
[IO.File]::WriteAllText("$exe.config", $config, (New-Object Text.UTF8Encoding($false)))
$notices = [IO.File]::ReadAllText((Join-Path $Src "THIRD_PARTY_NOTICES.txt"))
foreach ($l in $licenses) {
    $notices += "`r`n`r`n==== License file shipped in $($l.Package) ====`r`n`r`n" + $l.Text
}
[IO.File]::WriteAllText((Join-Path $stage "THIRD_PARTY_NOTICES.txt"), $notices, (New-Object Text.UTF8Encoding($false)))
[IO.File]::WriteAllText((Join-Path $stage "version.txt"), $version, (New-Object Text.UTF8Encoding($false)))

if (Test-Path -LiteralPath $OutDir) { Remove-Item -LiteralPath $OutDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Copy-Item -Path (Join-Path $stage "*") -Destination $OutDir
Get-ChildItem -LiteralPath $OutDir | Select-Object Name, Length
