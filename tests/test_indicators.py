import pandas as pd
from bot.indicators import normalize_candles, add_indicators

def test_normalize_and_add_indicators():
    n = 60
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="min"),
        "open": [100 + i for i in range(n)],
        "high": [101 + i for i in range(n)],
        "low": [99 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
        "volume": [10] * n,
    })
    result = add_indicators(normalize_candles(frame))
    assert len(result) == n
    assert "ema20" in result and "ema50" in result and "rsi14" in result
    assert pd.notna(result.iloc[-1]["ema50"])
