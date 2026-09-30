# Backtest stored ticks: count alternating YES/NO ask >= 90c moments
# Use -All to scan every window in the DB (default).
param(
    [switch]$All
)

$py = . (Join-Path $PSScriptRoot "_env.ps1") -Install
$argsList = @()
if ($All) { $argsList += "--all" }
Write-Host "Backtest: YES/NO ask >= 90c reversals (stored DB data)" -ForegroundColor Cyan
& $py (Join-Path $PSScriptRoot "backtest_reversal.py") @argsList @args
