# VPS / remote access: bind 0.0.0.0:8000, open Windows firewall, start collector + web
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

$env:PM_HOST = "0.0.0.0"
if (-not $env:PM_PORT) { $env:PM_PORT = "8000" }

$ruleName = "Polymarket Analysis (TCP $($env:PM_PORT))"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Opening Windows firewall port $($env:PM_PORT)..." -ForegroundColor Yellow
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Inbound `
        -LocalPort $env:PM_PORT `
        -Protocol TCP `
        -Action Allow | Out-Null
}

$ips = @(Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    ForEach-Object { $_.IPAddress } | Select-Object -Unique)

$py = . (Join-Path $PSScriptRoot "_env.ps1") -Install
Write-Host ""
Write-Host "Starting collector + web (all interfaces)..." -ForegroundColor Cyan
Write-Host "  Local:   http://127.0.0.1:$($env:PM_PORT)" -ForegroundColor Green
foreach ($ip in $ips) {
    Write-Host "  Remote:  http://${ip}:$($env:PM_PORT)" -ForegroundColor Green
}
if (-not $ips) {
    Write-Host "  Remote:  http://<your-vps-public-ip>:$($env:PM_PORT)" -ForegroundColor Green
}
Write-Host ""
Write-Host "  If remote access fails, also allow TCP $($env:PM_PORT) in your cloud provider security group." -ForegroundColor DarkGray
Write-Host "  Press Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""
& $py -m pmanalysis start
