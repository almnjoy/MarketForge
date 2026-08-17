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

echo ""
echo "Installed to $DEST"
echo ""
echo "Next: guided setup - it takes your Alpaca keys, checks them, and detects"
echo "whether your account has real-time data."
echo ""
cd "$DEST" && "$PY" _app/setup.py </dev/tty || \
  echo "  (run it later with:  cd $DEST && $PY _app/setup.py)"

echo ""
echo "  Start the desk:  MF_EMBEDDED=1 $PY $DEST/app.py  ->  http://localhost:8410"
echo "  Copilot:         open a terminal in $DEST and start your coding agent"
echo "  Docs:            https://docs.madeformeai.com/marketforge/index"
echo "  Community:       https://discord.gg/JE8TEYZp2f"
echo ""
