from __future__ import annotations
import numpy as np
import pandas as pd

REQUIRED = {"timestamp", "open", "high", "low", "close", "volume"}

def normalize_candles(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValueError("Missing CSV columns: " + ", ".join(sorted(missing)))
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=list(REQUIRED)).sort_values("timestamp")
    df = df.drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    if len(df) < 55:
        raise ValueError("At least 55 valid candles are required.")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive.")
    if (df["volume"] < 0).any():
        raise ValueError("Volume cannot be negative.")
    if (df["high"] < df[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("Invalid candle: high is below another OHLC value.")
    if (df["low"] > df[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Invalid candle: low is above another OHLC value.")
    return df

def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - 100 / (1 + rs)
    df.loc[(loss == 0) & (gain > 0), "rsi14"] = 100
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].rolling(30, min_periods=1).sum()
    df["vwap30"] = (typical * df["volume"]).rolling(30, min_periods=1).sum() / vol.replace(0, np.nan)
    avg = df["volume"].rolling(20, min_periods=5).mean()
    df["relative_volume"] = df["volume"] / avg.replace(0, np.nan)
    return df
