"""Independent confirmation layer for CoinDCX Expert Picks.

This module evaluates public candle data and pick levels only. It never places orders.
Thresholds are research hypotheses and must be evaluated with walk-forward testing.
"""
from __future__ import annotations
from datetime import date
from typing import Any
import pandas as pd
from bot.regime_strategy import assess_regime

STARTING_EQUITY_INR = 10_000.0
RISK_PER_TRADE_INR = 50.0
MAX_LEVERAGE = 3.0
DAILY_LOSS_STOP_INR = 200.0
MIN_REWARD_RISK = 1.2
FEE_RATE_PER_SIDE = 0.0005
SLIPPAGE_RATE_PER_SIDE = 0.0005

def _valid_ohlcv(frame: pd.DataFrame | None, minimum: int = 70) -> bool:
    if frame is None or not isinstance(frame, pd.DataFrame) or len(frame) < minimum:
        return False
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return False
    return not frame[list(required - {"timestamp"})].tail(minimum).isna().any().any()

def _net_reward_risk(side: str, entry: float, stop: float, target: float) -> float:
    if side == "LONG":
        risk, reward = entry - stop, target - entry
    else:
        risk, reward = stop - entry, entry - target
    if risk <= 0 or reward <= 0:
        return 0.0
    cost = entry * 2 * (FEE_RATE_PER_SIDE + SLIPPAGE_RATE_PER_SIDE)
    net_reward = reward - cost
    net_risk = risk + cost
    return max(0.0, net_reward) / net_risk if net_risk > 0 else 0.0

def evaluate_expert_pick(pick: dict[str, Any], price: float | None,
                         candles_5m: pd.DataFrame | None, candles_15m: pd.DataFrame | None,
                         *, today: date | None = None, spread_pct: float | None = None,
                         daily_realized_pnl_inr: float = 0.0,
                         position_already_open: bool = False) -> dict[str, Any]:
    """Return an auditable candidate/wait/reject decision, never an order."""
    today = today or date.today()
    if any(k not in pick for k in ("side", "low", "high", "stop", "target", "valid")):
        return {"decision": "REJECT", "reason": "Pick is missing required levels or expiry"}
    side = str(pick["side"]).upper()
    if side not in {"LONG", "SHORT"}:
        return {"decision": "REJECT", "reason": "Unsupported pick direction"}
    try:
        low, high, stop, target = (float(pick[k]) for k in ("low", "high", "stop", "target"))
    except (TypeError, ValueError):
        return {"decision": "REJECT", "reason": "Pick levels are not numeric"}
    if not (0 < low <= high and stop > 0 and target > 0):
        return {"decision": "REJECT", "reason": "Invalid or unordered price levels"}
    if side == "LONG" and not (stop < low and target > high):
        return {"decision": "REJECT", "reason": "Long stop/target are inconsistent with entry range"}
    if side == "SHORT" and not (stop > high and target < low):
        return {"decision": "REJECT", "reason": "Short stop/target are inconsistent with entry range"}
    try:
        expiry = date.fromisoformat(str(pick["valid"]))
    except ValueError:
        return {"decision": "REJECT", "reason": "Pick expiry is invalid"}
    if today > expiry:
        return {"decision": "REJECT", "reason": "Expert Pick has expired"}
    if price is None or not isinstance(price, (int, float)) or price <= 0:
        return {"decision": "WAIT", "reason": "Current price unavailable"}
    if position_already_open:
        return {"decision": "WAIT", "reason": "One-position rule: another paper position is open"}
    if daily_realized_pnl_inr <= -DAILY_LOSS_STOP_INR:
        return {"decision": "REJECT", "reason": "Daily realized-loss stop reached"}
    if spread_pct is not None and spread_pct > 0.15:
        return {"decision": "REJECT", "reason": "Spread exceeds 0.15% safety threshold"}
    if not low <= float(price) <= high:
        return {"decision": "WAIT", "reason": "Price is outside the Expert Pick entry range",
                "price_in_range": False}
    if not _valid_ohlcv(candles_5m) or not _valid_ohlcv(candles_15m):
        return {"decision": "WAIT", "reason": "Insufficient or invalid 5m/15m candle data",
                "price_in_range": True}
    model_5m = assess_regime(candles_5m, expert_pick=True)
    model_15m = assess_regime(candles_15m, expert_pick=True)
    d5, d15 = str(model_5m.get("decision", "SKIP")).upper(), str(model_15m.get("decision", "SKIP")).upper()
    rr = _net_reward_risk(side, float(price), stop, target)
    audit = {"price_in_range": True, "model_5m": d5, "regime_5m": model_5m.get("regime", "UNKNOWN"),
             "model_15m": d15, "regime_15m": model_15m.get("regime", "UNKNOWN"),
             "net_reward_risk_estimate": round(rr, 3)}
    if d5 != side:
        return {"decision": "WAIT", "reason": f"5m model does not confirm pick direction ({d5})", **audit}
    if d15 != side:
        return {"decision": "WAIT", "reason": f"15m confirmation conflicts with pick ({d15})", **audit}
    if rr < MIN_REWARD_RISK:
        return {"decision": "REJECT", "reason": f"Estimated net reward/risk {rr:.2f} is below {MIN_REWARD_RISK:.2f}", **audit}
    return {"decision": "ENTER_CANDIDATE",
            "reason": "Range, 5m signal, 15m confirmation and net reward/risk passed; paper execution checks still required",
            **audit}

def calculate_position_size(entry: float, stop: float, equity_inr: float = STARTING_EQUITY_INR,
                            risk_inr: float = RISK_PER_TRADE_INR, leverage: float = MAX_LEVERAGE,
                            fx_inr_per_quote: float = 88.0) -> dict[str, float]:
    """Indicative base quantity; contract multipliers and precision must be verified separately."""
    if min(entry, stop, equity_inr, risk_inr, leverage, fx_inr_per_quote) <= 0 or entry == stop:
        return {"quantity": 0.0, "risk_budget_inr": 0.0, "notional_inr": 0.0}
    risk_budget = min(risk_inr, equity_inr * 0.005)
    risk_quantity = (risk_budget / fx_inr_per_quote) / abs(entry - stop)
    leverage_quantity = (equity_inr / fx_inr_per_quote * min(leverage, MAX_LEVERAGE)) / entry
    quantity = max(0.0, min(risk_quantity, leverage_quantity))
    return {"quantity": quantity, "risk_budget_inr": risk_budget,
            "notional_inr": quantity * entry * fx_inr_per_quote}
