# PaperTrader - same brain as MoneyTrader, except:

**Read `MoneyTrader/00-BRAIN.md` first.** This file is deliberately short. Writing a
second full strategy here would guarantee the two drift, and then the mirror is not
a mirror.

## What is different

1. **It fires automatically.** Everything the live lane stages, this lane executes.
   No click. That is the point - it is the always-on shadow.
2. **It can short.** The live account is cash and under the $2,000 Reg T minimum, so
   shorting only exists here. This is a funding fact, not a strategy difference.
   When live is funded past $2k, shorts become live-able and nothing else changes.
3. **Size is PERCENTAGE-matched, never share-matched.**
   `paper_qty = live_notional / live_equity * paper_equity / price`
   Copying share counts produces a record of a different trade. 1 live share of a
   $113 stock in a $1,023 account is 11%; the twin is not 1 share of a $100k book.
4. **It is the system of record.** Live is what happened to the money; this is what
   happened to the idea, at a size where the sample is readable.

## What is NOT different
The setups, the gates, the stop logic. All of it lives in MoneyTrader.
