from datetime import date
import numpy as np
import pandas as pd
from bot.expert_pick_strategy_v2 import calculate_position_size, evaluate_expert_pick

def candles(n=120, trend=0.03):
    x = np.arange(n, dtype=float)
    close = 100 + trend * x + 0.3 * np.sin(x / 3)
    return pd.DataFrame({"timestamp": pd.date_range("2026-10-01", periods=n, freq="min", tz="UTC"),
        "open": close, "high": close + 0.2, "low": close - 0.2, "close": close,
        "volume": np.full(n, 100.0)})

def pick():
    return {"side":"LONG","low":100,"high":104,"stop":98,"target":108,"valid":"2026-10-23"}

def test_outside_range_waits():
    result=evaluate_expert_pick(pick(),105,candles(),candles(),today=date(2026,10,20))
    assert result["decision"]=="WAIT" and result["price_in_range"] is False

def test_expired_pick_rejected():
    result=evaluate_expert_pick(pick(),102,candles(),candles(),today=date(2026,10,24))
    assert result["decision"]=="REJECT" and "expired" in result["reason"].lower()

def test_invalid_long_levels_rejected():
    bad={**pick(),"stop":102,"target":99}
    assert evaluate_expert_pick(bad,102,candles(),candles(),today=date(2026,10,20))["decision"]=="REJECT"

def test_open_position_blocks_entry():
    result=evaluate_expert_pick(pick(),102,candles(),candles(),today=date(2026,10,20),position_already_open=True)
    assert result["decision"]=="WAIT"

def test_daily_stop_blocks_entry():
    result=evaluate_expert_pick(pick(),102,candles(),candles(),today=date(2026,10,20),daily_realized_pnl_inr=-200)
    assert result["decision"]=="REJECT"

def test_position_size_respects_caps():
    result=calculate_position_size(100,98)
    assert result["quantity"]>0 and result["risk_budget_inr"]<=50
    assert result["notional_inr"]<=30000+1e-6

def test_decision_layer_never_places_orders():
    result=evaluate_expert_pick(pick(),102,candles(),candles(),today=date(2026,10,20))
    assert "live_order" not in result
    assert result["decision"] in {"ENTER_CANDIDATE","WAIT","REJECT"}
