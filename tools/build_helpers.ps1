#Requires -Version 5.1
<#
.SYNOPSIS
  Helpers for build_release.ps1 (Windows PowerShell 5.1 compatible). Dot-source this file.

  Invoke-Native          Run a native command, stream its output, stop on a non-zero exit code.
  Assert-SafeBuildTarget Refuse a deletion target that is not strictly inside the build directory, or whose path
                         passes through a reparse point (junction, symbolic link, mount point).
  Remove-BuildOutput     Assert-SafeBuildTarget, refuse any reparse point inside the tree, then delete it.
  Assert-FreshFile       Refuse a build artifact that is missing, empty, or older than the build step that made it.

  Building never touches installed apps. These helpers only read and delete inside the build directory given to them.
#>

function Invoke-Native {
    <# Runs a native command and throws when it exits non-zero. PowerShell 5.1 does not stop on a native
       command's exit code even with $ErrorActionPreference = "Stop", so every native step goes through this. #>
    param(
        [Parameter(Mandatory = $true)][string]$Step,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )
    $global:LASTEXITCODE = 0
    & $FilePath @ArgumentList | Out-Host
    $code = $global:LASTEXITCODE
    if ($null -eq $code) { $code = 0 }
    if ($code -ne 0) {
        throw "Build step '$Step' failed: '$FilePath' exited with code $code."
    }
}

function Get-NormalizedFullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    # IsPathRooted also accepts D:relative and \root-relative on .NET Framework.
    # Neither identifies an absolute deletion target independently of shell state.
    if ($Path -notmatch '^(?:[A-Za-z]:[\\/]|\\\\(?![?.](?:\\|$))[^\\/]+\\[^\\/]+(?:\\|$))') {
        throw "Refusing path '$Path': a fully qualified drive or UNC path is required."
    }
    $full = [IO.Path]::GetFullPath($Path)
    # Keep a drive root such as "D:\" intact; strip trailing separators otherwise
    if ($full.Length -gt 3) { $full = $full.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) }
    return $full
}

function Initialize-BuildTools {
    # Resolve the prepared interpreter before refreshing tool discovery. Keep
    # virtual-environment and Developer PowerShell paths ahead of registry paths.
    $python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $paths = @($env:Path -split ';') +
        @([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';') +
        @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';') +
        @("$env:USERPROFILE\.cargo\bin")
    $env:Path = ($paths | Where-Object { $_ } | Select-Object -Unique) -join ';'
    return $python
}

function Test-IsReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Assert-SafeBuildTarget {
    <# Throws unless $Target is strictly inside $BuildRoot after full-path normalization, and neither $BuildRoot nor
       any existing directory between it and $Target (including $Target) is a reparse point. #>
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$BuildRoot
    )
    $root = Get-NormalizedFullPath $BuildRoot
    $full = Get-NormalizedFullPath $Target
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete '$full': it is not inside the build directory '$root'."
    }
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "Refusing to delete '$full': the build directory '$root' does not exist."
    }
    if (Test-IsReparsePoint $root) {
        throw "Refusing to delete '$full': the build directory '$root' is a junction or link."
    }
    $current = $root
    foreach ($part in $full.Substring($prefix.Length).Split([IO.Path]::DirectorySeparatorChar)) {
        $current = Join-Path $current $part
        if (Test-IsReparsePoint $current) {
            throw "Refusing to delete '$full': '$current' is a junction or link."
        }
    }
    return $full
}

function Remove-BuildOutput {
    <# Deletes $Target (a directory or file inside $BuildRoot). Refuses reparse points anywhere in the tree instead
       of following or deleting them, so a junction can never redirect the deletion outside the build directory. #>
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$BuildRoot
    )
    $full = Assert-SafeBuildTarget -Target $Target -BuildRoot $BuildRoot
    if (-not (Test-Path -LiteralPath $full)) { return }
    $item = Get-Item -LiteralPath $full -Force
    if ($item -is [IO.DirectoryInfo]) {
        $pending = New-Object 'System.Collections.Generic.Stack[System.IO.DirectoryInfo]'
        $pending.Push($item)
        while ($pending.Count -gt 0) {
            $dir = $pending.Pop()
            foreach ($child in $dir.GetFileSystemInfos()) {
                if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                    throw "Refusing to delete '$full': it contains the junction or link '$($child.FullName)'. Remove it by hand after checking where it points."
                }
                if ($child -is [IO.DirectoryInfo]) { $pending.Push($child) }
            }
        }
        [IO.Directory]::Delete($full, $true)
    } else {
        [IO.File]::Delete($full)
    }
}

function Assert-FreshFile {
    <# Throws unless $Path exists, is non-empty, and was written at or after $NotBeforeUtc (minus a 2 s allowance for
       file-system timestamp granularity). Returns the FileInfo. #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][datetime]$NotBeforeUtc,
        [Parameter(Mandatory = $true)][string]$What
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$What was not produced: '$Path' does not exist."
    }
    $file = Get-Item -LiteralPath $Path -Force
    if ($file.Length -le 0) {
        throw "$What is empty: '$Path'."
    }
    if ($file.LastWriteTimeUtc -lt $NotBeforeUtc.ToUniversalTime().AddSeconds(-2)) {
        throw "$What is stale: '$Path' was last written $($file.LastWriteTimeUtc.ToString('u')), before this build step started at $($NotBeforeUtc.ToUniversalTime().ToString('u'))."
    }
    return $file
}
