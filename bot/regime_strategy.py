from __future__ import annotations

import numpy as np
import pandas as pd

from bot.indicators import normalize_candles


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate causal 1-minute features for conservative regime selection."""
    df = normalize_candles(frame)
    close = df["close"]
    high, low = df["high"], df["low"]
    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()

    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    df["atr14"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr_safe = df["atr14"].replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_safe
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx14"] = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - 100 / (1 + rs)
    df.loc[(loss == 0) & (gain > 0), "rsi14"] = 100

    typical = (high + low + close) / 3
    vol_sum = df["volume"].rolling(30, min_periods=10).sum()
    df["vwap30"] = (typical * df["volume"]).rolling(30, min_periods=10).sum() / vol_sum.replace(0, np.nan)
    df["relative_volume"] = df["volume"] / df["volume"].rolling(20, min_periods=10).mean().replace(0, np.nan)
    df["atr_pct"] = df["atr14"] / close * 100
    return df


def assess_regime(frame: pd.DataFrame, symbol: str = "UNKNOWN", expert_pick: bool = True,
                  max_atr_pct: float = 0.8) -> dict:
    """Return a conservative regime and a paper-only candidate signal.

    Regime thresholds are initial hypotheses; they must be validated with walk-forward tests.
    """
    if not expert_pick:
        return {"symbol": symbol, "regime": "SKIP", "decision": "SKIP",
                "reason": "Not selected by Expert Picks"}
    try:
        df = _features(frame)
    except (ValueError, KeyError) as exc:
        return {"symbol": symbol, "regime": "SKIP", "decision": "SKIP",
                "reason": f"Invalid candle data: {exc}"}
    if len(df) < 60:
        return {"symbol": symbol, "regime": "SKIP", "decision": "SKIP",
                "reason": "At least 60 one-minute candles are required"}

    row = df.iloc[-1]
    fields = ["close", "ema20", "ema50", "atr14", "adx14", "rsi14", "vwap30", "relative_volume", "atr_pct"]
    if any(pd.isna(row.get(key)) for key in fields):
        return {"symbol": symbol, "regime": "SKIP", "decision": "SKIP",
                "reason": "Indicators not ready"}
    close, ema20, ema50 = (float(row[k]) for k in ("close", "ema20", "ema50"))
    atr, adx, rsi = float(row["atr14"]), float(row["adx14"]), float(row["rsi14"])
    vwap, relvol, atr_pct = (float(row[k]) for k in ("vwap30", "relative_volume", "atr_pct"))
    if atr <= 0 or atr_pct > max_atr_pct or relvol < 0.7 or relvol > 4.0:
        return {"symbol": symbol, "regime": "SKIP", "decision": "SKIP",
                "reason": "Volatility or volume outside configured safety bounds",
                "adx14": adx, "atr_pct": atr_pct, "relative_volume": relvol}

    separation_atr = abs(ema20 - ema50) / atr
    # Trend must be strong enough and directional; require a pullback close to EMA20,
    # then a close back on the trend side. This is a candidate rule, not a proven edge.
    if adx >= 22 and separation_atr >= 0.20:
        if ema20 > ema50 and close > ema50 and close >= vwap and 48 <= rsi <= 68:
            return {"symbol": symbol, "regime": "TREND", "decision": "LONG",
                    "reason": "Uptrend confirmed; momentum and VWAP filters pass",
                    "close": close, "ema20": ema20, "ema50": ema50, "atr14": atr,
                    "adx14": adx, "rsi14": rsi, "vwap30": vwap,
                    "relative_volume": relvol, "atr_pct": atr_pct}
        if ema20 < ema50 and close < ema50 and close <= vwap and 32 <= rsi <= 52:
            return {"symbol": symbol, "regime": "TREND", "decision": "SHORT",
                    "reason": "Downtrend confirmed; momentum and VWAP filters pass",
                    "close": close, "ema20": ema20, "ema50": ema50, "atr14": atr,
                    "adx14": adx, "rsi14": rsi, "vwap30": vwap,
                    "relative_volume": relvol, "atr_pct": atr_pct}
        return {"symbol": symbol, "regime": "TREND", "decision": "SKIP",
                "reason": "Trend detected but entry confirmation is absent",
                "adx14": adx, "atr_pct": atr_pct, "relative_volume": relvol}

    # Mean reversion is allowed only in a weak-trend regime and at an outer band.
    if adx <= 18 and separation_atr <= 0.15:
        distance = (close - vwap) / atr
        if distance <= -1.0 and rsi <= 32:
            decision, reason = "LONG", "Range regime; downside extension may be reverting"
        elif distance >= 1.0 and rsi >= 68:
            decision, reason = "SHORT", "Range regime; upside extension may be reverting"
        else:
            decision, reason = "SKIP", "Range detected but price is not at an extreme"
        return {"symbol": symbol, "regime": "RANGE", "decision": decision,
                "reason": reason, "close": close, "atr14": atr, "adx14": adx,
                "rsi14": rsi, "vwap30": vwap, "relative_volume": relvol,
                "atr_pct": atr_pct}

    return {"symbol": symbol, "regime": "UNCLEAR", "decision": "SKIP",
            "reason": "Regime is mixed; conservative no-trade rule applied",
            "adx14": adx, "atr_pct": atr_pct, "relative_volume": relvol}
