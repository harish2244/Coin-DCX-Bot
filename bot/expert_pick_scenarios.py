from __future__ import annotations

from typing import Any

STARTING_EQUITY_INR = 10_000.0
RISK_PER_TRADE_INR = 50.0  # 0.5% of starting equity
LEVERAGE_CAP = 3.0
MAX_NOTIONAL_INR = STARTING_EQUITY_INR * LEVERAGE_CAP
FEE_RATE_PER_SIDE = 0.0005
EXIT_SLIPPAGE_RATE = 0.0005

PICKS = [
    {
        "pick_id": "marketwind-nikkei225-2026-10-15-2110",
        "instrument": "Nikkei225",
        "direction": "LONG",
        "entry_low": 63450.0,
        "entry_high": 64400.0,
        "stop_loss": 62200.0,
        "take_profit": 66168.0,
        "valid_until": "2026-10-19",
    },
    {
        "pick_id": "marketwind-xau-2026-10-19",
        "instrument": "XAU",
        "direction": "SHORT",
        "entry_low": 4181.0,
        "entry_high": 4244.0,
        "stop_loss": 4291.0,
        "take_profit": 4110.45,
        "valid_until": "2026-10-23",
    },
]


def evaluate_entry(pick: dict[str, Any], entry: float) -> dict[str, Any]:
    """Evaluate hypothetical stop/target outcomes using ₹50 risk and a 3x notional cap.

    This is a price-level scenario, not a backtest: it assumes the entry fills exactly
    at the chosen price and the stop or target fills as specified, with modeled costs.
    """
    side = str(pick["direction"]).upper()
    stop, target = float(pick["stop_loss"]), float(pick["take_profit"])
    if side == "LONG" and not stop < entry < target:
        raise ValueError("LONG requires stop < entry < target")
    if side == "SHORT" and not target < entry < stop:
        raise ValueError("SHORT requires target < entry < stop")

    stop_distance_pct = abs(entry - stop) / entry
    target_distance_pct = abs(target - entry) / entry
    desired_notional = RISK_PER_TRADE_INR / stop_distance_pct
    notional = min(desired_notional, MAX_NOTIONAL_INR)
    qty = notional / entry
    actual_stop_risk = qty * abs(entry - stop)

    def outcome(exit_reason: str) -> dict[str, float]:
        raw_exit = stop if exit_reason == "STOP" else target
        exit_price = raw_exit * (1 - EXIT_SLIPPAGE_RATE if side == "LONG" else 1 + EXIT_SLIPPAGE_RATE)
        gross = (exit_price - entry) * qty * (1 if side == "LONG" else -1)
        costs = entry * qty * FEE_RATE_PER_SIDE + exit_price * qty * FEE_RATE_PER_SIDE
        return {
            "gross_pnl_inr": round(gross, 2),
            "estimated_costs_inr": round(costs, 2),
            "net_pnl_inr": round(gross - costs, 2),
        }

    return {
        "entry": round(entry, 4),
        "entry_notional_inr": round(notional, 2),
        "quantity_in_contract_units": round(qty, 8),
        "planned_stop_risk_before_costs_inr": round(actual_stop_risk, 2),
        "stop_distance_pct": round(stop_distance_pct * 100, 3),
        "target_distance_pct": round(target_distance_pct * 100, 3),
        "reward_risk_ratio_before_costs": round(target_distance_pct / stop_distance_pct, 3),
        "win_at_target": outcome("TARGET"),
        "loss_at_stop": outcome("STOP"),
        "entry_eligible_under_3x_cap": notional <= MAX_NOTIONAL_INR + 1e-8,
    }


def build_scenarios() -> dict[str, Any]:
    results = []
    for pick in PICKS:
        low, high = float(pick["entry_low"]), float(pick["entry_high"])
        entries = [low, (low + high) / 2, high]
        results.append({
            **pick,
            "scenario_type": "hypothetical_price_level_scenarios_not_backtest",
            "scenarios": [evaluate_entry(pick, entry) for entry in entries],
        })
    return {
        "generated_by": "bot.expert_pick_scenarios",
        "mode": "PAPER_ONLY",
        "assumptions": {
            "starting_equity_inr": STARTING_EQUITY_INR,
            "risk_per_trade_inr": RISK_PER_TRADE_INR,
            "risk_pct_of_starting_equity": 0.5,
            "leverage_cap": LEVERAGE_CAP,
            "max_notional_inr": MAX_NOTIONAL_INR,
            "fee_per_side_pct": FEE_RATE_PER_SIDE * 100,
            "exit_slippage_pct": EXIT_SLIPPAGE_RATE * 100,
            "fee_and_slippage_note": "Approximation for scenario comparison; actual instrument contract multipliers, settlement currency, funding, spread, and exchange fees are unverified.",
            "strategy_gate": "Only simulate a trade if its published entry range is reached while the call is valid and the independent technical/regime model confirms the direction. One position at a time; stop after ₹200 daily realized loss. No averaging down.",
            "probability_note": "No win probability is inferred from these price levels. Win/loss outcomes are conditional scenarios, not forecasts or historical backtest results.",
            "instrument_mapping_note": "Nikkei225 and XAU market IDs and contract specifications are not yet verified in the bot's market-data feed; no live monitoring or actual trade is recorded.",
        },
        "picks": results,
    }
