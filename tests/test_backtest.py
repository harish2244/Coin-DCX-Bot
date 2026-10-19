import numpy as np
import pandas as pd
import pytest

from bot.backtest import run_backtest


def sample_candles(n=100):
    x = np.arange(n, dtype=float)
    close = 100 + 0.03 * x + 0.35 * np.sin(x / 2)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-10-01", periods=n, freq="min", tz="UTC"),
        "open": close,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": np.full(n, 100.0),
    })


def test_backtest_result_has_expected_metrics_and_ledger():
    result = run_backtest(sample_candles(), symbol="B-BTC_USDT")
    assert "return_pct" in result
    assert "max_drawdown_pct" in result
    assert "trades_frame" in result
    assert result["starting_equity_quote"] == 10_000


def test_backtest_rejects_short_history():
    with pytest.raises(ValueError):
        run_backtest(sample_candles(60))


def test_backtest_does_not_use_future_candles_for_signal():
    base = sample_candles()
    changed = base.copy()
    changed.loc[90:, ["open", "high", "low", "close"]] *= 1.5
    # Decisions before candle 90 should be unchanged; compare entries that occur before it.
    first = run_backtest(base)["trades_frame"]
    second = run_backtest(changed)["trades_frame"]
    first = first[first["entry_time"] < str(base.loc[90, "timestamp"])]
    second = second[second["entry_time"] < str(base.loc[90, "timestamp"])]
    assert first[["entry_time", "side"]].reset_index(drop=True).equals(
        second[["entry_time", "side"]].reset_index(drop=True)
    )
