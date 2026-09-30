# Remove corrupt ticks where both YES and NO ask >= 90c
$py = . (Join-Path $PSScriptRoot "_env.ps1") -Install
Write-Host "Removing impossible quote ticks from DB..." -ForegroundColor Cyan
& $py (Join-Path $PSScriptRoot "cleanup_bad_ticks.py") @args
