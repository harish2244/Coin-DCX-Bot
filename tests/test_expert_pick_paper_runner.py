from bot.expert_pick_paper_runner import PICKS, rr

def test_pick_levels_are_directionally_valid():
    for p in PICKS:
        if p["side"] == "LONG":
            assert p["stop"] < p["low"] < p["high"] < p["target"]
        else:
            assert p["target"] < p["low"] < p["high"] < p["stop"]

def test_xau_target_reward_risk_filter():
    p=next(x for x in PICKS if x["instrument"]=="XAU")
    assert rr(p,4182.01) < 1.2
