# Parses (never runs) every tracked PowerShell script, so a syntax error in build_release.ps1, dev.ps1,
# stop_dev.ps1 or the build helpers fails CI before anyone tries a build.
# Works in Windows PowerShell 5.1 (what build_release.ps1 requires) and PowerShell 7.
# Usage: powershell -NoProfile -File .github/scripts/ps_parse_check.ps1
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$files = @(& git -C $root ls-files -- "*.ps1" "*.psm1" "*.psd1")
if ($LASTEXITCODE -ne 0) { throw "git ls-files failed" }
$bad = 0
foreach ($file in $files) {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root $file), [ref]$tokens, [ref]$errors)
    foreach ($e in $errors) {
        $bad++
        Write-Output ("::error file={0},line={1},col={2}::{3}" -f $file, $e.Extent.StartLineNumber, $e.Extent.StartColumnNumber, $e.Message)
    }
}
Write-Output ("Parsed {0} PowerShell files: {1} syntax error(s)." -f $files.Count, $bad)
if ($bad -gt 0) { exit 1 }
