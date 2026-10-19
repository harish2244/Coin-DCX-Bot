from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from bot.coindcx_public import get_active_futures_instruments, get_futures_candles
from bot.regime_strategy import assess_regime

OUT = Path("docs/data/expert-pick-replay.json")
START = pd.Timestamp("2026-10-15T00:00:00Z")
STARTING_EQUITY_INR = 10_000.0
RISK_INR = 50.0
DAILY_STOP_INR = 200.0
LEVERAGE_CAP = 3.0
INR_PER_STABLECOIN = 88.0  # provisional estimate for paper calculations only
FEE_RATE = 0.0005
SLIPPAGE_RATE = 0.0005
MIN_REWARD_RISK = 1.2

PICKS = [
    {"instrument": "Nikkei225", "direction": "LONG", "low": 63450.0, "high": 64400.0,
     "stop": 62200.0, "target": 66168.0, "valid": "2026-10-19",
     "terms": ["NIKKEI", "JPN225", "JP225", "NI225"]},
    {"instrument": "XAU", "direction": "SHORT", "low": 4181.0, "high": 4244.0,
     "stop": 4291.0, "target": 4110.45, "valid": "2026-10-23",
     "terms": ["XAU", "GOLD", "XAUUSD"]},
]


def _reward_risk(pick: dict, entry: float) -> float:
    risk = abs(entry - float(pick["stop"]))
    reward = abs(float(pick["target"]) - entry)
    return reward / risk if risk > 0 else 0.0


def _pnl_for_trade(pick: dict, side: str, entry_raw: float, exit_raw: float,
                   reason: str, equity_inr: float) -> dict:
    """Linear-contract estimate only; actual CoinDCX contract multiplier must be verified."""
    direction = 1 if side == "LONG" else -1
    stop_distance = abs(entry_raw - float(pick["stop"]))
    if stop_distance <= 0 or entry_raw <= 0:
        return {"status": "SKIPPED_INVALID_RISK"}
    risk_stable = min(RISK_INR, equity_inr * 0.005) / INR_PER_STABLECOIN
    qty_risk = risk_stable / stop_distance
    qty_leverage = (equity_inr / INR_PER_STABLECOIN * LEVERAGE_CAP) / entry_raw
    qty = min(qty_risk, qty_leverage)
    entry = entry_raw * (1 + SLIPPAGE_RATE if side == "LONG" else 1 - SLIPPAGE_RATE)
    exit_price = exit_raw * (1 - SLIPPAGE_RATE if side == "LONG" else 1 + SLIPPAGE_RATE)
    gross_stable = (exit_price - entry) * qty * direction
    costs_stable = (entry * qty + exit_price * qty) * FEE_RATE
    net_stable = gross_stable - costs_stable
    return {
        "exit_reason": reason, "entry_price": entry_raw, "exit_price": exit_raw,
        "quantity_linear_contract_assumption": qty, "gross_pnl_inr_estimate": round(gross_stable * INR_PER_STABLECOIN, 2),
        "fees_slippage_inr_estimate": round((costs_stable + abs(entry-entry_raw)*qty + abs(exit_price-exit_raw)*qty) * INR_PER_STABLECOIN, 2),
        "net_pnl_inr_estimate": round(net_stable * INR_PER_STABLECOIN, 2),
        "position_notional_inr_estimate": round(entry_raw * qty * INR_PER_STABLECOIN, 2),
        "sizing_risk_budget_inr": round(min(RISK_INR, equity_inr * 0.005), 2),
    }


def _replay_pick(pick: dict, pair: str, df: pd.DataFrame) -> dict:
    end = pd.Timestamp(pick["valid"] + "T23:59:59Z")
    df = df[(df.timestamp >= START) & (df.timestamp <= end)].copy().reset_index(drop=True)
    if len(df) < 70:
        return {"instrument": pick["instrument"], "pair": pair, "status": "INSUFFICIENT_HISTORY",
                "candles_checked": len(df), "message": "Not enough 1-minute candles inside the pick validity window."}

    range_touches = raw_signals = confirmations = entries_count = 0
    eligible_rr_skips = 0
    signals = []
    trades = []
    pos = None
    equity = STARTING_EQUITY_INR
    daily_pnl: dict[str, float] = {}
    daily_stopped: set[str] = set()
    i = 60

    while i < len(df) - 1:
        candle = df.iloc[i]
        day = pd.Timestamp(candle.timestamp).date().isoformat()
        touch = float(candle.low) <= pick["high"] and float(candle.high) >= pick["low"]
        if touch:
            range_touches += 1
        model = assess_regime(df.iloc[:i + 1], pair, expert_pick=True)
        side = str(model.get("decision", "SKIP")).upper()
        if side in {"LONG", "SHORT"}:
            raw_signals += 1
        confirmed = touch and side == pick["direction"]
        if confirmed:
            confirmations += 1
            signals.append({"time": candle.timestamp.isoformat(), "close": float(candle.close),
                            "decision": side, "regime": model.get("regime")})

        # Exit any existing position using this candle's range. If stop and target both hit,
        # conservatively record the stop. No same-candle entry/exit.
        if pos is not None:
            hi, lo = float(candle.high), float(candle.low)
            if pos["side"] == "LONG":
                reason = "STOP" if lo <= pos["stop"] else ("TARGET" if hi >= pos["target"] else None)
            else:
                reason = "STOP" if hi >= pos["stop"] else ("TARGET" if lo <= pos["target"] else None)
            if reason:
                exit_raw = pos["stop"] if reason == "STOP" else pos["target"]
                trade = _pnl_for_trade(pick, pos["side"], pos["entry_raw"], exit_raw, reason, equity)
                trade.update({"instrument": pick["instrument"], "pair": pair, "entry_time": pos["entry_time"],
                              "exit_time": candle.timestamp.isoformat(), "reward_risk_at_entry": pos["rr"]})
                trades.append(trade)
                equity += trade.get("net_pnl_inr_estimate", 0.0)
                daily_pnl[day] = daily_pnl.get(day, 0.0) + trade.get("net_pnl_inr_estimate", 0.0)
                if daily_pnl[day] <= -DAILY_STOP_INR:
                    daily_stopped.add(day)
                pos = None
            i += 1
            continue

        # Signals are known only at candle close; simulate entry at next candle OPEN.
        if confirmed and day not in daily_stopped:
            next_bar = df.iloc[i + 1]
            entry = float(next_bar.open)
            rr = _reward_risk(pick, entry)
            if (pick["direction"] == "LONG" and not (pick["stop"] < entry < pick["target"])) or (
                pick["direction"] == "SHORT" and not (pick["target"] < entry < pick["stop"])
            ):
                i += 1
                continue
            if rr < MIN_REWARD_RISK:
                eligible_rr_skips += 1
                i += 1
                continue
            pos = {"side": side, "entry_raw": entry, "stop": float(pick["stop"]),
                   "target": float(pick["target"]), "entry_time": next_bar.timestamp.isoformat(), "rr": rr}
            entries_count += 1
            i += 1
            continue
        i += 1

    if pos is not None:
        trades.append({"instrument": pick["instrument"], "pair": pair, "side": pos["side"],
                       "entry_time": pos["entry_time"], "entry_price": pos["entry_raw"],
                       "exit_time": None, "exit_price": None, "exit_reason": "OPEN_AT_END",
                       "net_pnl_inr_estimate": None, "reward_risk_at_entry": pos["rr"]})

    closed = [t for t in trades if t.get("net_pnl_inr_estimate") is not None]
    wins = [t for t in closed if t["net_pnl_inr_estimate"] > 0]
    losses = [t for t in closed if t["net_pnl_inr_estimate"] < 0]
    gross_profit = sum(t["net_pnl_inr_estimate"] for t in wins)
    gross_loss = -sum(t["net_pnl_inr_estimate"] for t in losses)
    return {
        "instrument": pick["instrument"], "pair": pair, "status": "REPLAYED",
        "window_start": str(START), "window_end": end.isoformat(), "candles_checked": len(df),
        "range_touch_candles": range_touches, "raw_directional_signals": raw_signals,
        "range_touch_plus_model_confirmations": confirmations, "reward_risk_rejections": eligible_rr_skips,
        "entry_count": entries_count, "closed_trade_count": len(closed),
        "wins": len(wins), "losses": len(losses), "win_rate_pct": round(100 * len(wins) / len(closed), 2) if closed else None,
        "net_pnl_inr_estimate": round(sum(t["net_pnl_inr_estimate"] for t in closed), 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else (None if not gross_profit else "infinite"),
        "ending_equity_inr_estimate": round(STARTING_EQUITY_INR + sum(t["net_pnl_inr_estimate"] for t in closed), 2),
        "trades": trades, "confirmed_signals": signals,
        "cost_model": {"fee_rate_per_side": FEE_RATE, "slippage_rate_per_side": SLIPPAGE_RATE,
                       "stablecoin_to_inr_assumption": INR_PER_STABLECOIN},
        "important_limitations": [
            "INR P&L is an estimate assuming a linear contract and provisional ₹88 per USDT/USDC.",
            "CoinDCX contract multiplier/settlement for this exact market is not verified; do not treat this as exchange-accurate P&L.",
            "The daily stop is applied to closed P&L within each instrument replay; cross-instrument shared account scheduling needs a combined replay.",
            "Only candle history available from the public API and pick validity window is tested."
        ]
    }


def main() -> None:
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "PAPER_ONLY_NO_ORDERS",
        "strategy_rules": {"starting_equity_inr": STARTING_EQUITY_INR, "risk_per_trade_inr": RISK_INR,
                           "daily_loss_stop_inr": DAILY_STOP_INR, "max_leverage": LEVERAGE_CAP,
                           "minimum_reward_risk": MIN_REWARD_RISK, "fees_per_side": FEE_RATE,
                           "slippage_per_side": SLIPPAGE_RATE},
        "method": "Causal 1-minute replay; signal after candle close, entry next candle open; exits on later candle high/low; if stop and target touch together, stop wins.",
        "picks": []
    }
    active = []
    errors = {}
    for margin in ("USDT", "USDC", "INR"):
        try:
            active.extend(get_active_futures_instruments(margin))
        except Exception as exc:
            errors[margin] = str(exc)
    active = sorted(set(active))
    if errors:
        out["instrument_lookup_warnings"] = errors

    for pick in PICKS:
        matches = [s for s in active if any(term in s.upper() for term in pick["terms"])]
        if len(matches) != 1:
            out["picks"].append({"instrument": pick["instrument"], "status": "BLOCKED_UNVERIFIED_MARKET_ID",
                                 "candidate_matches": matches, "message": "No unique matching market ID; no results fabricated."})
            continue
        pair = matches[0]
        try:
            candles = get_futures_candles(pair, resolution="1", lookback_minutes=10000)
            out["picks"].append(_replay_pick(pick, pair, candles))
        except Exception as exc:
            out["picks"].append({"instrument": pick["instrument"], "pair": pair, "status": "DATA_ERROR",
                                 "message": str(exc)})

    out["overall_status"] = "COMPLETE" if out["picks"] and all(p.get("status") == "REPLAYED" for p in out["picks"]) else "PARTIAL_OR_BLOCKED"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
