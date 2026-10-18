import pandas as pd
import pytest

from bot import coindcx_public as api


def test_active_instruments_validates_margin_currency():
    with pytest.raises(ValueError):
        api.get_active_futures_instruments("BTC")


def test_active_instruments_filters_and_sorts(monkeypatch):
    monkeypatch.setattr(api, "_get_json", lambda *args, **kwargs: ["B-ETH_USDT", "bad", "B-BTC_USDT", "B-ETH_USDT"])
    assert api.get_active_futures_instruments("USDT") == ["B-BTC_USDT", "B-ETH_USDT"]


def test_futures_candles_converts_timestamp_and_sorts(monkeypatch):
    monkeypatch.setattr(api, "_get_json", lambda *args, **kwargs: {
        "s": "ok",
        "data": [
            {"time": 1780000060000, "open": 11, "high": 12, "low": 10, "close": 11.5, "volume": 2},
            {"time": 1780000000000, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1},
        ],
    })
    frame = api.get_futures_candles("B-BTC_USDT", "1", 120)
    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(frame) == 2
    assert frame.iloc[0]["close"] == 10.5
    assert pd.api.types.is_datetime64_any_dtype(frame["timestamp"])


def test_futures_candles_rejects_bad_resolution():
    with pytest.raises(ValueError):
        api.get_futures_candles("B-BTC_USDT", "2", 120)
