# Lane routing index - the copilot reads THIS, not all three brains

Three lanes, two brains. Load the index every turn; read a lane's `00-BRAIN.md`
only when the question routes there. Loading all three every turn is what makes a
turn take five minutes.

| Ask about... | Lane | Read |
|---|---|---|
| today's movers, low float, 1-minute setups, day trading | **DayTrader** | `TheTradingBrains/DayTrader/` |
| my positions, swing setups, what to buy/hold, real money | **MoneyTrader** | `TheTradingBrains/MoneyTrader/` |
| shorts, the study record, "what would have happened" | **PaperTrader** | `TheTradingBrains/PaperTrader/` |

**MoneyTrader and PaperTrader are the SAME BRAIN.** PaperTrader states only what it
overrides. If a question is about the strategy, read MoneyTrader; if it is about how
paper behaves differently, read PaperTrader too.

Cross-lane questions ("total exposure", "am I doubled up on one theme") belong to
the copilot, which is the only thing that spans lanes.
