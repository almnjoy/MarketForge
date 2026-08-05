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
if (-not (Test-Path (Join-Path $dest "bot\.env"))) {
  Copy-Item (Join-Path $dest "bot\.env.template") (Join-Path $dest "bot\.env")
}

Write-Host ""
Write-Host "Installed to $dest" -ForegroundColor Green
Write-Host "  1) Add your Alpaca PAPER keys to bot\.env   (free keys: alpaca.markets)"
Write-Host "  2) Double-click run-portable.bat            ->  http://localhost:8410"
Write-Host "  3) Optional: Claude Code CLI in that folder = your live copilot"
Write-Host "  Full guide: PORTABLE.md - community: https://discord.gg/JE8TEYZp2f"
Write-Host ""
Start-Process explorer $dest
