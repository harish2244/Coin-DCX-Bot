from __future__ import annotations

def position_plan(equity: float, entry: float, stop: float, leverage: float,
                  risk_fraction_pct: float = 0.5) -> dict:
    if min(equity, entry, stop, leverage) <= 0:
        raise ValueError("Equity, entry, stop and leverage must be positive.")
    if entry == stop:
        raise ValueError("Stop must differ from entry.")
    if not 0 < risk_fraction_pct <= 1:
        raise ValueError("Risk percentage must be >0 and <=1.")
    risk_cash = equity * risk_fraction_pct / 100
    qty = risk_cash / abs(entry - stop)
    max_notional = equity * leverage
    notional = qty * entry
    capped = notional > max_notional
    if capped:
        qty = max_notional / entry
        notional = max_notional
        risk_cash = qty * abs(entry - stop)
    return {"quantity_base_asset": qty, "notional": notional,
            "planned_stop_loss_before_costs": risk_cash,
            "max_notional": max_notional, "size_capped_by_leverage": capped}
