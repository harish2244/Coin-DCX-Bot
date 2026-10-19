from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from bot.regime_strategy import assess_regime
from bot.indicators import normalize_candles


def run_backtest(
    candles: pd.DataFrame,
    *,
    symbol: str = "UNKNOWN",
    starting_equity: float = 10_000.0,
    risk_pct: float = 0.5,
    leverage: float = 3.0,
    daily_loss_limit: float = 200.0,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0005,
    stop_atr: float = 1.0,
    target_r: float = 1.5,
    max_hold_bars: int = 5,
    expert_pick: bool = True,
) -> dict:
    """Causal OHLCV backtest. Equity is denominated in the candle quote currency.

    Signals are calculated using a completed candle and enter at the next candle open.
    Stops/targets are checked with subsequent candle high/low; if both hit, stop wins.
    """
    if starting_equity <= 0 or not 0 < risk_pct <= 1 or leverage <= 0:
        raise ValueError("Invalid equity, risk_pct, or leverage")
    if daily_loss_limit <= 0 or fee_rate < 0 or slippage_rate < 0:
        raise ValueError("Invalid daily loss limit or cost rate")
    if stop_atr <= 0 or target_r <= 0 or max_hold_bars < 1:
        raise ValueError("Invalid stop, target, or holding period")

    df = normalize_candles(candles)
    if len(df) < 70:
        raise ValueError("At least 70 one-minute candles are required for a meaningful warm-up")
    df = df.reset_index(drop=True)
    equity = float(starting_equity)
    peak_equity = equity
    max_drawdown_pct = 0.0
    daily_realized: dict[str, float] = {}
    trades: list[dict] = []
    i = 60

    while i < len(df) - 1:
        signal = assess_regime(df.iloc[: i + 1], symbol=symbol, expert_pick=expert_pick)
        side = signal.get("decision")
        if side not in {"LONG", "SHORT"}:
            i += 1
            continue

        entry_bar = i + 1
        entry_raw = float(df.iloc[entry_bar]["open"])
        atr = float(signal.get("atr14", np.nan))
        if not np.isfinite(atr) or atr <= 0 or entry_raw <= 0:
            i += 1
            continue
        direction = 1 if side == "LONG" else -1
        stop_distance = stop_atr * atr
        stop = entry_raw - direction * stop_distance
        if stop <= 0:
            i += 1
            continue
        target = entry_raw + direction * target_r * stop_distance
        risk_cash = equity * risk_pct / 100.0
        qty = min(risk_cash / stop_distance, equity * leverage / entry_raw)
        if qty <= 0:
            i += 1
            continue

        # Adverse entry slippage and entry fee are included in net P&L.
        entry = entry_raw * (1 + slippage_rate if side == "LONG" else 1 - slippage_rate)
        entry_fee = entry * qty * fee_rate
        entry_day = pd.Timestamp(df.iloc[entry_bar]["timestamp"]).date().isoformat()
        exit_reason, exit_raw, exit_idx = "TIME", float(df.iloc[entry_bar]["close"]), entry_bar
        last_idx = min(len(df) - 1, entry_bar + max_hold_bars - 1)
        for j in range(entry_bar, last_idx + 1):
            candle = df.iloc[j]
            hi, lo = float(candle["high"]), float(candle["low"])
            if side == "LONG":
                if lo <= stop:
                    exit_reason, exit_raw, exit_idx = "STOP", stop, j
                    break
                if hi >= target:
                    exit_reason, exit_raw, exit_idx = "TARGET", target, j
                    break
            else:
                if hi >= stop:
                    exit_reason, exit_raw, exit_idx = "STOP", stop, j
                    break
                if lo <= target:
                    exit_reason, exit_raw, exit_idx = "TARGET", target, j
                    break
            exit_raw, exit_idx = float(candle["close"]), j

        exit_price = exit_raw * (1 - slippage_rate if side == "LONG" else 1 + slippage_rate)
        exit_fee = exit_price * qty * fee_rate
        gross = (exit_price - entry) * qty * direction
        costs = entry_fee + exit_fee
        net = gross - costs
        exit_day = pd.Timestamp(df.iloc[exit_idx]["timestamp"]).date().isoformat()
        daily_realized[exit_day] = daily_realized.get(exit_day, 0.0) + net
        equity += net
        peak_equity = max(peak_equity, equity)
        max_drawdown_pct = max(max_drawdown_pct, (peak_equity - equity) / peak_equity * 100 if peak_equity else 0)
        trades.append({
            "symbol": symbol, "side": side, "regime": signal.get("regime"),
            "signal_time": str(df.iloc[i]["timestamp"]), "entry_time": str(df.iloc[entry_bar]["timestamp"]),
            "exit_time": str(df.iloc[exit_idx]["timestamp"]), "entry": entry, "exit": exit_price,
            "stop": stop, "target": target, "quantity": qty, "exit_reason": exit_reason,
            "gross_pnl": gross, "costs": costs, "net_pnl": net, "equity_after": equity,
        })
        if daily_realized.get(exit_day, 0.0) <= -daily_loss_limit:
            # No new entries for the rest of this UTC date.
            while i < len(df) - 1 and pd.Timestamp(df.iloc[i]["timestamp"]).date().isoformat() == exit_day:
                i += 1
        else:
            i = exit_idx + 1

    trade_frame = pd.DataFrame(trades)
    if trade_frame.empty:
        wins = losses = 0
        gross_profit = gross_loss = 0.0
        profit_factor = 0.0
        win_rate = 0.0
        avg_net = 0.0
    else:
        pnls = trade_frame["net_pnl"]
        wins = int((pnls > 0).sum())
        losses = int((pnls < 0).sum())
        gross_profit = float(pnls[pnls > 0].sum())
        gross_loss = float(-pnls[pnls < 0].sum())
        profit_factor = gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0)
        win_rate = wins / len(trade_frame) * 100
        avg_net = float(pnls.mean())

    return {
        "symbol": symbol, "starting_equity_quote": starting_equity, "ending_equity_quote": equity,
        "net_pnl_quote": equity - starting_equity, "return_pct": (equity / starting_equity - 1) * 100,
        "trades": len(trades), "wins": wins, "losses": losses, "win_rate_pct": win_rate,
        "profit_factor": profit_factor, "expectancy_per_trade_quote": avg_net,
        "max_drawdown_pct": max_drawdown_pct, "fees_and_slippage_quote": float(trade_frame["costs"].sum()) if not trade_frame.empty else 0.0,
        "trades_frame": trade_frame,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the experimental regime-switching paper strategy on 1m OHLCV CSV.")
    parser.add_argument("csv", type=Path, help="CSV with timestamp,open,high,low,close,volume")
    parser.add_argument("--symbol", default="UNKNOWN")
    parser.add_argument("--equity-quote", type=float, default=10_000.0,
                        help="Starting equity in candle quote currency (not automatically INR-converted)")
    parser.add_argument("--trades-out", type=Path, default=Path("backtest_trades.csv"))
    args = parser.parse_args()
    result = run_backtest(pd.read_csv(args.csv), symbol=args.symbol, starting_equity=args.equity_quote)
    trades = result.pop("trades_frame")
    for key, value in result.items():
        print(f"{key}: {value}")
    trades.to_csv(args.trades_out, index=False)
    print(f"Trade ledger saved to: {args.trades_out}")


if __name__ == "__main__":
    main()
