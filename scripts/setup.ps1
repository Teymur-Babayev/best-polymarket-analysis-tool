# One-time install into system Python
$py = . (Join-Path $PSScriptRoot "_env.ps1") -Install
Write-Host "Done. Run:" -ForegroundColor Green
Write-Host "  .\scripts\collector.ps1"
Write-Host "  .\scripts\web.ps1"
