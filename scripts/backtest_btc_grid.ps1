# BTC tiered grid: entry1=90c, entry2=80-90, entry3=70-90 (231 txt files)
$py = . (Join-Path $PSScriptRoot "_env.ps1") -Install
Write-Host "BTC grid backtest (11 x 21 parameters)..." -ForegroundColor Cyan
& $py (Join-Path $PSScriptRoot "backtest_btc_grid.py") @args
