from __future__ import annotations

import json
import os
from pathlib import Path

from bot.backtest import run_backtest
from bot.coindcx_public import get_futures_candles


def main() -> None:
    symbols = [s.strip() for s in os.getenv("BACKTEST_SYMBOLS", "B-BTC_USDT,B-ETH_USDT").split(",") if s.strip()]
    lookback = int(os.getenv("BACKTEST_LOOKBACK_MINUTES", "10000"))
    inr_equity = float(os.getenv("STARTING_EQUITY_INR", "10000"))
    inr_per_usdt = float(os.getenv("INR_PER_USDT", "88"))
    if inr_per_usdt <= 0:
        raise ValueError("INR_PER_USDT must be positive")
    quote_equity = inr_equity / inr_per_usdt
    out_dir = Path("backtest_results")
    out_dir.mkdir(exist_ok=True)
    summaries = []
    for symbol in symbols:
        try:
            candles = get_futures_candles(symbol, resolution="1", lookback_minutes=lookback)
            result = run_backtest(
                candles,
                symbol=symbol,
                starting_equity=quote_equity,
                risk_pct=0.5,
                leverage=3.0,
                daily_loss_limit=200 / inr_per_usdt,
                fee_rate=0.0005,
                slippage_rate=0.0005,
                stop_atr=1.0,
                target_r=1.5,
                max_hold_bars=5,
            )
            trades = result.pop("trades_frame")
            trades.to_csv(out_dir / f"{symbol.replace('/', '_')}_trades.csv", index=False)
            result["starting_equity_inr_equivalent"] = inr_equity
            result["ending_equity_inr_equivalent"] = result["ending_equity_quote"] * inr_per_usdt
            result["net_pnl_inr_equivalent"] = result["net_pnl_quote"] * inr_per_usdt
            result["fx_assumption_inr_per_usdt"] = inr_per_usdt
            result["candles"] = len(candles)
            summaries.append(result)
        except Exception as exc:
            summaries.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
    (out_dir / "summary.json").write_text(json.dumps(summaries, indent=2, allow_nan=True))
    print(json.dumps(summaries, indent=2, allow_nan=True))
    if not summaries or all("error" in row for row in summaries):
        raise SystemExit("No symbol completed a backtest; see summary.json")


if __name__ == "__main__":
    main()
