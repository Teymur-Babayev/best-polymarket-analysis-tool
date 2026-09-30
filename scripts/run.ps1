# Both together — collector + web in one process (listens on 0.0.0.0 by default)
$py = . (Join-Path $PSScriptRoot "_env.ps1") -Install
$port = if ($env:PM_PORT) { $env:PM_PORT } else { "8000" }
Write-Host "Starting collector + web..." -ForegroundColor Cyan
Write-Host "  Local: http://127.0.0.1:$port" -ForegroundColor Green
Write-Host "  Remote: use this machine's IP, e.g. http://<ip>:$port" -ForegroundColor Green
Write-Host "  VPS setup: .\scripts\run-vps.ps1 (opens firewall too)" -ForegroundColor DarkGray
Write-Host "  Press Ctrl+C to stop" -ForegroundColor DarkGray
& $py -m pmanalysis start
