from bot.risk import position_plan

def test_position_plan_respects_risk_budget():
    plan = position_plan(10_000, entry=100, stop=99, leverage=3, risk_fraction_pct=0.5)
    assert plan["planned_stop_loss_before_costs"] == 50
    assert plan["notional"] == 5_000
    assert plan["size_capped_by_leverage"] is False

def test_position_plan_caps_notional_by_leverage():
    plan = position_plan(10_000, entry=100, stop=99.9, leverage=3, risk_fraction_pct=0.5)
    assert plan["notional"] <= 30_000
    assert plan["size_capped_by_leverage"] is True

def test_position_plan_rejects_equal_entry_and_stop():
    try:
        position_plan(10_000, entry=100, stop=100, leverage=3)
    except ValueError:
        return
    raise AssertionError("Expected ValueError")
