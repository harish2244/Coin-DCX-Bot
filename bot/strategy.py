from __future__ import annotations
import pandas as pd

def assess_candidate(frame: pd.DataFrame, symbol: str, expert_pick: bool = True) -> dict:
    if frame.empty:
        return {"symbol": symbol, "decision": "SKIP", "reason": "No candle data"}
    row = frame.iloc[-1]
    fields = ["close", "ema20", "ema50", "rsi14", "vwap30", "relative_volume"]
    if any(pd.isna(row.get(k)) for k in fields):
        return {"symbol": symbol, "decision": "SKIP", "reason": "Indicators unavailable"}
    close, e20, e50 = float(row.close), float(row.ema20), float(row.ema50)
    rsi, vwap = float(row.rsi14), float(row.vwap30)
    relvol = float(row.relative_volume) if pd.notna(row.relative_volume) else 0.0
    long_checks = {
        "EMA20 above EMA50": e20 > e50, "close above EMA20": close > e20,
        "close above rolling VWAP": close > vwap, "RSI 50–70": 50 <= rsi <= 70,
        "relative volume >= 1": relvol >= 1,
    }
    short_checks = {
        "EMA20 below EMA50": e20 < e50, "close below EMA20": close < e20,
        "close below rolling VWAP": close < vwap, "RSI 30–50": 30 <= rsi <= 50,
        "relative volume >= 1": relvol >= 1,
    }
    ls, ss = sum(long_checks.values()), sum(short_checks.values())
    if not expert_pick:
        decision, checks = "SKIP", {}
        reason = "Not marked as an Expert Pick"
    elif ls == len(long_checks):
        decision, checks, reason = "LONG", long_checks, "All configured long checks passed"
    elif ss == len(short_checks):
        decision, checks, reason = "SHORT", short_checks, "All configured short checks passed"
    else:
        decision, checks = "SKIP", long_checks if ls >= ss else short_checks
        reason = "Checks failed: " + ", ".join(k for k, v in checks.items() if not v)
    return {"symbol": symbol, "decision": decision, "reason": reason, "close": close,
            "ema20": e20, "ema50": e50, "rsi14": rsi, "vwap30": vwap,
            "relative_volume": relvol, "long_score": ls, "short_score": ss}
