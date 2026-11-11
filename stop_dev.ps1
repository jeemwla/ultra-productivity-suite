#Requires -Version 5.1
<# Leaves dev mode and goes back to the installed Ultra Suite. #>
$R = $PSScriptRoot
foreach ($exe in "$R\ultra_dock\src-tauri\target\debug\ultra-suite.exe", "$R\ultra_dock\src-tauri\target\release\ultra-suite.exe") {
    if ((Test-Path $exe) -and (Get-Process | Where-Object { $_.Path -eq $exe })) { Start-Process $exe -ArgumentList "--quit"; Start-Sleep 2 }
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'watchfiles|suite_core' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
foreach ($port in 8081, 8084, 8091, 8093, 8300, 8301) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
$installed = "$env:LOCALAPPDATA\Ultra Suite\Ultra Suite.exe"
if (Test-Path $installed) { Start-Process explorer.exe -ArgumentList "`"$installed`""; Write-Host "Installed Ultra Suite started." -ForegroundColor Green }
