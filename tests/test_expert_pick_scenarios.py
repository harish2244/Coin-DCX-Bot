import pytest

from bot.expert_pick_scenarios import PICKS, RISK_PER_TRADE_INR, evaluate_entry, build_scenarios


def test_scenario_outputs_respect_risk_and_leverage_caps():
    for pick in PICKS:
        for entry in (pick["entry_low"], (pick["entry_low"] + pick["entry_high"]) / 2, pick["entry_high"]):
            result = evaluate_entry(pick, entry)
            assert result["entry_notional_inr"] <= 30_000
            assert result["planned_stop_risk_before_costs_inr"] <= RISK_PER_TRADE_INR + 1e-6
            assert result["win_at_target"]["net_pnl_inr"] > 0
            assert result["loss_at_stop"]["net_pnl_inr"] < 0


def test_reward_risk_changes_across_each_entry_range():
    nikkei, xau = PICKS
    assert evaluate_entry(nikkei, nikkei["entry_low"])["reward_risk_ratio_before_costs"] > evaluate_entry(nikkei, nikkei["entry_high"])["reward_risk_ratio_before_costs"]
    assert evaluate_entry(xau, xau["entry_high"])["reward_risk_ratio_before_costs"] > evaluate_entry(xau, xau["entry_low"])["reward_risk_ratio_before_costs"]


def test_bad_entry_side_is_rejected():
    with pytest.raises(ValueError):
        evaluate_entry(PICKS[0], 62000)


def test_scenario_payload_is_explicitly_not_a_backtest():
    payload = build_scenarios()
    assert payload["mode"] == "PAPER_ONLY"
    assert len(payload["picks"]) == 2
    assert "not_backtest" in payload["picks"][0]["scenario_type"]
