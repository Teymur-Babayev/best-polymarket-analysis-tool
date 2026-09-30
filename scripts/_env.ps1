# Use system Python — no venv.
param([switch]$Install)

$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

$Python = "python"

if ($Install) {
    Write-Host "Installing dependencies (system Python)..." -ForegroundColor Yellow
    & $Python -m pip install -e . -q
}

return $Python
