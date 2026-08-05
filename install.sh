#!/usr/bin/env bash
# Market Forge installer (macOS / Linux) - https://madeformeai.com/marketforge
#   curl -fsSL https://madeformeai.com/marketforge/install.sh | bash
set -euo pipefail
echo ""
echo "  MARKET FORGE - Your AI trading desk"
echo "  open source - runs on your machine - paper by default"
echo ""

command -v git >/dev/null || { echo "Missing: git. Install it and rerun."; exit 1; }
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "Missing: Python 3.10+. Install it and rerun."; exit 1; }

DEST="$HOME/MarketForge"
if [ -d "$DEST/.git" ]; then
  echo "Updating existing install at $DEST"
  git -C "$DEST" pull --ff-only
else
  git clone https://github.com/almnjoy/MarketForge.git "$DEST"
fi

"$PY" -m pip install -r "$DEST/requirements.txt" --quiet || \
  "$PY" -m pip install -r "$DEST/requirements.txt" --quiet --break-system-packages
[ -f "$DEST/bot/.env" ] || cp "$DEST/bot/.env.template" "$DEST/bot/.env"

echo ""
echo "Installed to $DEST"
echo "  1) Add your Alpaca PAPER keys to bot/.env   (free keys: alpaca.markets)"
echo "  2) MF_EMBEDDED=1 $PY $DEST/app.py           ->  http://localhost:8410"
echo "  3) Optional: Claude Code CLI in that folder = your live copilot"
echo "  Full guide: $DEST/PORTABLE.md - community: https://discord.gg/JE8TEYZp2f"
echo ""
