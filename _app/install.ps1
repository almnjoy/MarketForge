# Market Forge installer (Windows) - https://madeformeai.com/marketforge
#   powershell -c "irm https://madeformeai.com/marketforge/install.ps1 | iex"
$ErrorActionPreference = "Stop"
Write-Host ""
Write-Host "  MARKET FORGE - Your AI trading desk" -ForegroundColor Cyan
Write-Host "  open source - runs on your machine - paper by default" -ForegroundColor DarkGray
Write-Host ""

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host "Missing: git. Install from https://git-scm.com then rerun." -ForegroundColor Yellow; exit 1
}
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) {
  Write-Host "Missing: Python 3.10+. Install from https://python.org (check 'Add to PATH') then rerun." -ForegroundColor Yellow; exit 1
}

$dest = Join-Path $env:USERPROFILE "MarketForge"
if (Test-Path (Join-Path $dest ".git")) {
  Write-Host "Updating existing install at $dest"
  git -C $dest pull --ff-only
} else {
  git clone https://github.com/almnjoy/MarketForge.git $dest
}

& $py.Source -m pip install -r (Join-Path $dest "requirements.txt") --quiet

Write-Host ""
Write-Host "Installed to $dest" -ForegroundColor Green
Write-Host ""
Write-Host "Next: guided setup - it takes your Alpaca keys, checks them, and detects" -ForegroundColor Cyan
Write-Host "whether your account has real-time data." -ForegroundColor Cyan
Write-Host ""
Push-Location $dest
& $py.Source _app\setup.py
Pop-Location

Write-Host ""
Write-Host "  Start the desk:  _app\run-portable.bat   ->  http://localhost:8410"
Write-Host "  Copilot:         open a terminal in $dest and start your coding agent"
Write-Host "  Docs:            https://docs.madeformeai.com/marketforge/index"
Write-Host "  Community:       https://discord.gg/JE8TEYZp2f"
Write-Host ""
Start-Process explorer $dest
