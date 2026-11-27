# Deploys Vasco to Vercel production, but only if every test passes.
#   powershell -ExecutionPolicy Bypass -File deploy.ps1
# Steps: backend tests (pytest, needs local MySQL) -> frontend tests -> frontend build -> vercel deploy --prod
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'

function Invoke-Step($name, [scriptblock]$cmd) {
    Write-Host "`n==> $name" -ForegroundColor Cyan
    & $cmd
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ECHEC : $name. Deploiement annule." -ForegroundColor Red
        exit 1
    }
}

Invoke-Step 'Tests backend (pytest)' { python -m pytest tests -q }
Push-Location react-app
try {
    Invoke-Step 'Tests frontend' { npm test --silent }
    Invoke-Step 'Build frontend' { npm run build --silent }
} finally { Pop-Location }
Invoke-Step 'Deploiement Vercel (production)' { vercel deploy --prod --yes }
Write-Host "`nOK : Vasco est en ligne sur https://vasco-omega.vercel.app" -ForegroundColor Green
