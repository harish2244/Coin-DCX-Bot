# Expert Pick Strategy v2 — research specification

**Mode: paper only. No live exchange orders.**

Expert Picks are candidate levels, not orders. The independent model validates the market and direction.

## Entry gates
1. Pick has a verified market ID, valid numeric levels and a non-expired validity date.
2. Public price is inside the supplied entry range.
3. Completed 5-minute candles produce a LONG/SHORT signal matching the pick.
4. Completed 15-minute candles independently confirm the same direction.
5. Stop and target are on the correct side of the entry range.
6. Estimated net reward/risk after a simple fee/slippage allowance is at least 1.2.
7. No other paper position is open, the daily realized-loss stop has not been reached, and spread/data quality pass safety checks.

Any failed gate means WAIT or REJECT, not a forced trade. An ENTER_CANDIDATE result is not an order: the paper execution layer must still check sizing, contract multiplier, quantity precision, and current price.

## Initial risk settings
- Starting paper equity: INR 10,000
- Planned risk budget: INR 50 per trade, capped at 0.5% of current estimated equity
- Maximum planned leverage: 3x
- Daily realized-loss stop: INR 200
- One open paper position at a time
- Minimum estimated net reward/risk: 1.2

Position sizing is indicative until market contract multiplier, quote currency, and precision are verified. Never use a pick's advertised leverage if it exceeds the bot's 3x cap.

## Exit and test protocol
For paper positions, check stop and target against completed candles; if both are touched within the same candle, assume the stop is hit first (conservative ambiguity rule). Record fees, slippage, exit reason, and whether a trade was rejected by each gate.

Evaluate on chronological train/validation/test periods and walk-forward windows. Report trade count, net P&L after costs, win rate, profit factor, maximum drawdown, expectancy per trade, and results by instrument. Do not optimize thresholds on the final holdout set. Do not call the strategy profitable based on a small sample.

## Important limitations
This module is the decision layer, not yet a complete runner integration. The live paper runner must supply correctly resampled, closed 5m and 15m candles, a verified market ID, actual spread/contract specifications, and persistent state before using its candidate decisions. No live trading is implemented or authorized by this module.
