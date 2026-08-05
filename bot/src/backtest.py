"""Walk-forward backtest of the trend-pullback strategy.

Reuses the REAL signal engine (signals.py) so this tests the actual strategy the
bot trades, not a parallel reimplementation. No lookahead: the entry signal at
day t is computed from bars[:t] (through the prior close) and fills at day t's
OPEN. Exits: the ATR stop (intraday, gap-down fills at the open) or a trend-break
(close below the slow SMA, fills next close). One position at a time per symbol.

Pure Python, no vectorbt/pandas dependency (keeps the bot's minimalism). The
point of this file is one honest question: does the signal beat buy-and-hold?

All prices are integer cents; returns are decimals (0.05 = +5%).
"""
from __future__ import annotations

import argparse

import config
import signals
from alpaca_client import AlpacaClient


# ==========================================================================
# PURE CORE
# ==========================================================================
def _close_trade(trades, pos, exit_idx, exit_price, exit_t, reason):
    entry = pos["entry"]
    pnl = (exit_price - entry) / entry if entry else 0.0
    trades.append({
        "entry_t": pos["entry_t"], "exit_t": exit_t,
        "entry_cents": entry, "exit_cents": exit_price,
        "pnl_pct": pnl, "bars_held": exit_idx - pos["entry_idx"],
        "reason": reason,
    })


def simulate(bars, cfg=config):
    """Run the strategy over one symbol's bar history. Returns a summary dict."""
    need = max(cfg.SMA_SLOW, cfg.ATR_PERIOD + 1, cfg.PULLBACK_SMA + 1)
    trades, pos = [], None

    if len(bars) <= need + 1:
        return summarize(trades, bars, need, cfg)

    for t in range(need, len(bars)):
        bar = bars[t]
        if pos is not None:
            # 1) stop hit intraday (gap-down fills at the open, not the stop)
            if bar["l"] <= pos["stop"]:
                fill = min(pos["stop"], bar["o"])
                _close_trade(trades, pos, t, fill, bar["t"], "stop")
                pos = None
                continue
            # 2) trend-break exit on the close
            if signals.exit_signal(bars[: t + 1], cfg)["exit"]:
                _close_trade(trades, pos, t, bar["c"], bar["t"], "trend_break")
                pos = None
                continue
        else:
            # entry signal as of the PRIOR close; fill at today's open
            sig = signals.signal(bars[:t], cfg)
            if sig["action"] == "buy" and sig["stop_cents"] and sig["stop_cents"] < bar["o"]:
                pos = {"entry_idx": t, "entry": bar["o"], "stop": sig["stop_cents"],
                       "entry_t": bar["t"]}

    if pos is not None:  # mark any open position to the final close
        _close_trade(trades, pos, len(bars) - 1, bars[-1]["c"], bars[-1]["t"], "open_end")

    return summarize(trades, bars, need, cfg)


def summarize(trades, bars, need, cfg=config):
    n = len(trades)
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]

    # strategy return: compound sequential trade returns (one position at a time)
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for t in trades:
        equity *= (1 + t["pnl_pct"])
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak else 0.0)
    strat_return = equity - 1.0

    # buy-and-hold over the same evaluable window
    bh = 0.0
    if len(bars) > need:
        start_px, end_px = bars[need]["c"], bars[-1]["c"]
        bh = (end_px - start_px) / start_px if start_px else 0.0

    return {
        "n_trades": n,
        "win_rate": (len(wins) / n) if n else 0.0,
        "avg_win_pct": (sum(t["pnl_pct"] for t in wins) / len(wins)) if wins else 0.0,
        "avg_loss_pct": (sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0.0,
        "avg_bars_held": (sum(t["bars_held"] for t in trades) / n) if n else 0.0,
        "strategy_return_pct": strat_return,
        "buy_hold_return_pct": bh,
        "max_drawdown_pct": max_dd,
        "beats_buy_hold": strat_return > bh,
        "trades": trades,
    }


# ==========================================================================
# I/O SHELL
# ==========================================================================
def load_universe():
    if config.UNIVERSE_PATH.exists():
        syms = [s.strip().upper() for s in config.UNIVERSE_PATH.read_text().splitlines()
                if s.strip() and not s.strip().startswith("#")]
        if syms:
            return syms
    return list(config.DEFAULT_UNIVERSE)


def main():
    ap = argparse.ArgumentParser(description="Backtest the trend-pullback strategy.")
    ap.add_argument("--symbol", help="backtest one symbol")
    ap.add_argument("--bars", type=int, default=1250, help="daily bars to pull (~5y)")
    args = ap.parse_args()

    client = AlpacaClient()
    universe = [args.symbol.upper()] if args.symbol else load_universe()
    print(config.env_banner())
    print(f"backtesting {len(universe)} symbol(s) over ~{args.bars} bars "
          f"(feed={config._get('ALPACA_DATA_FEED','iex')})\n")
    print(f"{'symbol':<7}{'trades':>7}{'win%':>7}{'strat':>9}{'buy&hold':>10}{'maxDD':>8}  edge")

    rows, beat = [], 0
    agg_strat, agg_bh = [], []
    for sym in universe:
        try:
            bars = client.get_daily_bars(sym, limit=args.bars)
        except Exception as e:
            print(f"{sym:<7}  error: {str(e)[:60]}")
            continue
        r = simulate(bars)
        rows.append((sym, r))
        agg_strat.append(r["strategy_return_pct"])
        agg_bh.append(r["buy_hold_return_pct"])
        if r["beats_buy_hold"]:
            beat += 1
        edge = "beats B&H" if r["beats_buy_hold"] else "lags"
        print(f"{sym:<7}{r['n_trades']:>7}{r['win_rate']*100:>6.0f}%"
              f"{r['strategy_return_pct']*100:>8.1f}%{r['buy_hold_return_pct']*100:>9.1f}%"
              f"{r['max_drawdown_pct']*100:>7.1f}%  {edge}")

    if rows:
        n = len(rows)
        print("\n" + "=" * 60)
        print(f"universe: {n} names | strategy beat buy-and-hold on {beat}/{n}")
        print(f"mean strategy return : {sum(agg_strat)/n*100:>7.1f}%")
        print(f"mean buy&hold return : {sum(agg_bh)/n*100:>7.1f}%")
        total_trades = sum(r['n_trades'] for _, r in rows)
        print(f"total trades         : {total_trades}")
        print("\nReminder: a backtest is the FLOOR, not proof. In-sample, no slippage/")
        print("commission modeled beyond fills, and past != future. If it can't beat")
        print("buy-and-hold here, it won't live. If it can, that's necessary not sufficient.")


if __name__ == "__main__":
    main()
