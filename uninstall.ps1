#Requires -Version 5.1
<# Removes everything Setup added outside this folder. Your .env and alarms in this folder are kept. #>
$Root = $PSScriptRoot
& "$Root\stop_all.ps1" -IncludeDock
Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "Ultra Suite.lnk") -ErrorAction SilentlyContinue
Remove-Item "$env:LOCALAPPDATA\UltraSuite" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$env:APPDATA\UltraSuiteDock" -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path "$env:APPDATA\deck\deckDeck") { python -I "$Root\03_deck_deck_config\generate_deck_profile.py" --remove }
foreach ($p in 8443, 8444) { if (Get-Command tailscale -ErrorAction SilentlyContinue) { tailscale serve --https=$p off *> $null } }
if (Get-ScheduledTask -TaskName "UltraSuite LibreHardwareMonitor" -ErrorAction SilentlyContinue) {
    Write-Host "Windows will ask for admin approval to remove the sensor sign-in task." -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile", "-Command",
        "Unregister-ScheduledTask -TaskName 'UltraSuite LibreHardwareMonitor' -Confirm:`$false"
}
Write-Host "Ultra Productivity Suite removed. Delete this folder to remove the rest." -ForegroundColor Green
