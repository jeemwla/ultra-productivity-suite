#Requires -Version 5.1
<# Stops everything start_all_windows.ps1 started (leaves LibreHardwareMonitor and Tailscale running). #>
param([switch]$IncludeDock)
foreach ($port in 8091, 8093, 8081, 8084) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
if ($IncludeDock) { Get-Process ultra-dock -ErrorAction SilentlyContinue | Stop-Process -Force }
Write-Host "Suite stopped." -ForegroundColor Green
