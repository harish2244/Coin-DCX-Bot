import pandas as pd

from bot.expert_pick_replay import _pnl_for_trade, _reward_risk


def test_xau_pick_reward_risk_is_below_minimum():
    pick = {"stop": 4291.0, "target": 4110.45}
    assert round(_reward_risk(pick, 4182.01), 3) == 0.657


def test_linear_paper_pnl_respects_risk_sizing_and_costs():
    pick = {"stop": 4291.0, "target": 4110.45}
    result = _pnl_for_trade(pick, "SHORT", 4182.01, 4291.0, "STOP", 10000)
    assert result["sizing_risk_budget_inr"] == 50
    assert result["net_pnl_inr_estimate"] < 0
    assert result["fees_slippage_inr_estimate"] > 0


def test_instrument_margin_accepts_usdc(monkeypatch):
    from bot import coindcx_public

    monkeypatch.setattr(coindcx_public, "_get_json", lambda url, params=None: ["B-NIKKEI_USDC"])
    assert coindcx_public.get_active_futures_instruments("USDC") == ["B-NIKKEI_USDC"]
