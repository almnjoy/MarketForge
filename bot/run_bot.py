"""Market Forge - embedded bot runner (no Docker, no cron).

Runs the stock-bot engine locally: the Flask read/trade API plus a scheduler
thread that fires the catalyst radar at 10a/12p/2p/4p ET on weekdays (the same
cadence a cron would use).

Config comes from bot/.env (copy .env.template, add YOUR Alpaca keys).
Everything is stdlib except Flask (pip install flask).
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # very old python: fall back to local time
    ET = None

BOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT / "src"))

import config  # noqa: E402  (this import also loads bot/.env)

SCAN_HOURS = config.RADAR_SCAN_HOURS   # ET, weekdays. Set RADAR_SCAN_HOURS in bot/.env


def _now_et():
    return datetime.now(ET) if ET else datetime.now()


def _radar_scheduler():
    """Fire src/radar.py at the top of each scan hour, weekdays only."""
    fired = set()
    print(f"[scheduler] radar at {sorted(SCAN_HOURS)}:00 ET weekdays "
          f"(env={config.STOCK_ENV}, LLM={'on' if config.RADAR_USE_LLM else 'off'})")
    while True:
        now = _now_et()
        key = (now.date(), now.hour)
        if (now.weekday() < 5 and now.hour in SCAN_HOURS
                and now.minute < 10 and key not in fired):
            fired.add(key)
            print(f"[scheduler] {now:%H:%M} ET - running radar scan")
            try:
                subprocess.run([sys.executable, str(BOT / "src" / "radar.py")],
                               cwd=str(BOT), timeout=600)
            except Exception as e:
                print(f"[scheduler] radar failed: {e}")
        if len(fired) > 40:
            fired = {k for k in fired if k[0] == now.date()}
        time.sleep(60)


def main():
    (BOT / "data").mkdir(exist_ok=True)
    if not (BOT / ".env").exists():
        print("!" * 64)
        print("bot/.env is missing. Copy bot/.env.template to bot/.env and add")
        print("your Alpaca keys (start with the PAPER pair). Then rerun.")
        print("!" * 64)
        sys.exit(1)
    threading.Thread(target=_radar_scheduler, daemon=True).start()
    import api  # noqa: E402
    print(f"Market Forge bot engine -> http://127.0.0.1:{config.API_PORT} "
          f"(env={config.STOCK_ENV.upper()})")
    api.app.run(host="127.0.0.1", port=config.API_PORT)


if __name__ == "__main__":
    main()
