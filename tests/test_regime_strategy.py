import numpy as np
import pandas as pd

from bot.regime_strategy import assess_regime


def candles(n=120, trend=0.0, amplitude=0.4):
    x = np.arange(n, dtype=float)
    close = 100 + trend * x + amplitude * np.sin(x / 2)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="min", tz="UTC"),
        "open": close,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": np.full(n, 100.0),
    })


def test_expert_pick_gate_skips():
    result = assess_regime(candles(), symbol="B-BTC_USDT", expert_pick=False)
    assert result["decision"] == "SKIP"
    assert result["regime"] == "SKIP"


def test_invalid_or_short_data_skips():
    result = assess_regime(candles(40), symbol="B-BTC_USDT")
    assert result["decision"] == "SKIP"


def test_decision_is_valid_and_never_requests_live_execution():
    result = assess_regime(candles(trend=0.03), symbol="B-BTC_USDT")
    assert result["decision"] in {"LONG", "SHORT", "SKIP"}
    assert result["regime"] in {"TREND", "RANGE", "UNCLEAR", "SKIP"}
    assert "live_order" not in result
