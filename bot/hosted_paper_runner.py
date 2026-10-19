from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from bot.coindcx_public import get_futures_candles, get_futures_current_prices
from bot.indicators import add_indicators, normalize_candles
from bot.paper_engine import PersistentPaperEngine
from bot.regime_strategy import assess_regime
from bot.strategy import assess_candidate
from bot.trade_journal import TradeJournal

DB_PATH = Path("data/live_paper_trades.sqlite3")
OUTPUT_PATH = Path("docs/data/dashboard.json")
STARTING_EQUITY = 10_000.0
RISK_PCT = 0.5
LEVERAGE = 3.0
DAILY_LOSS_LIMIT = 200.0
SYMBOLS = ["B-BTC_INR", "B-ETH_INR"]
LOOKBACK_MINUTES = 1440
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def safe_number(value):
    try:
        number = float(value)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def export_dashboard(engine: PersistentPaperEngine, journal: TradeJournal, status: dict) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with journal._connect() as db:
        signals = pd.read_sql_query(
            "SELECT timestamp,symbol,candle_time,price,raw_signal,model_decision,confirmed,regime,raw_reason,model_reason,rsi,adx,atr_pct,relative_volume FROM signal_events ORDER BY timestamp DESC LIMIT 500",
            db,
        ).to_dict(orient="records")
        trades = pd.read_sql_query(
            "SELECT id,symbol,side,quantity,entry,stop,target,opened_at,status,exit_price,exit_reason,gross_pnl,costs,net_pnl,closed_at FROM paper_trades ORDER BY id DESC LIMIT 500",
            db,
        ).to_dict(orient="records")
        total_signals = int(db.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0])
        confirmed_signals = int(db.execute("SELECT COUNT(*) FROM signal_events WHERE confirmed=1").fetchone()[0])
    summary = journal.summary(STARTING_EQUITY)
    summary["signals"] = total_signals
    summary["confirmed_signals"] = confirmed_signals
    engine_summary = engine.summary()
    summary["daily_net_pnl"] = engine_summary["daily_net_pnl"]
    summary["daily_stop_hit"] = engine_summary["daily_stop_hit"]
    summary["starting_equity"] = STARTING_EQUITY
    summary["equity_estimate"] = STARTING_EQUITY + float(summary["net_pnl"])
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "mode": "PAPER ONLY",
        "settings": {
            "symbols": SYMBOLS, "starting_equity_inr": STARTING_EQUITY,
            "planned_risk_pct": RISK_PCT, "leverage_cap": LEVERAGE,
            "daily_loss_stop_inr": DAILY_LOSS_LIMIT, "schedule": "Every 5 minutes (GitHub Actions)",
        },
        "summary": summary,
        "signals": [{k: (bool(v) if k == "confirmed" else safe_number(v) if k in {"price","rsi","adx","atr_pct","relative_volume"} else v) for k,v in row.items()} for row in signals],
        "trades": [{k: (safe_number(v) if k in {"quantity","entry","stop","target","exit_price","gross_pnl","costs","net_pnl"} else v) for k,v in row.items()} for row in trades],
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = PersistentPaperEngine(
        db_path=DB_PATH, starting_equity=STARTING_EQUITY, risk_pct=RISK_PCT,
        leverage=LEVERAGE, daily_loss_limit=DAILY_LOSS_LIMIT,
    )
    journal = TradeJournal(DB_PATH)
    status = {"ok": True, "message": "Scheduled run started", "processed_candles": 0, "errors": []}
    try:
        prices_payload = get_futures_current_prices()
        price_map = prices_payload.get("prices", {})
        for symbol in SYMBOLS:
            try:
                raw = get_futures_candles(symbol, resolution="1", lookback_minutes=LOOKBACK_MINUTES)
                candles = normalize_candles(raw)
                now_minute = pd.Timestamp(datetime.now(timezone.utc)).floor("min")
                candles = candles[candles["timestamp"] < now_minute].reset_index(drop=True)
                if len(candles) < 65:
                    raise RuntimeError(f"Only {len(candles)} completed candles; need at least 65")
                existing = journal.signals(limit=10000)
                old = existing.loc[existing["symbol"] == symbol, "candle_time"]
                last_seen = pd.to_datetime(old, utc=True, errors="coerce").max() if not old.empty else pd.NaT
                if pd.notna(last_seen):
                    new_indices = [i for i, ts in enumerate(candles["timestamp"]) if ts > last_seen]
                else:
                    new_indices = list(range(60, len(candles)))
                if not new_indices:
                    logging.info("%s: no new completed candles", symbol)
                    continue

                # A signal is recorded on its candle and can only enter at the NEXT candle's open.
                for i in new_indices:
                    if i < 60:
                        continue
                    candle = candles.iloc[i]
                    candle_time = pd.Timestamp(candle["timestamp"]).isoformat()
                    closed = engine.process_candle(
                        symbol, float(candle["high"]), float(candle["low"]), float(candle["close"]),
                        timestamp=candle_time,
                    )
                    if closed:
                        logging.info("%s paper close: %s net=%.2f", symbol, closed["exit_reason"], closed["net_pnl"])

                    # Evaluate the previous candle and enter at this candle's open, after processing existing positions.
                    if not closed and i >= 61 and not engine.summary()["has_open_position"]:
                        previous = candles.iloc[:i].reset_index(drop=True)
                        previous_indicators = add_indicators(previous)
                        raw_prev = assess_candidate(previous_indicators, symbol, expert_pick=True)
                        model_prev = assess_regime(previous, symbol, expert_pick=True)
                        side_prev = str(raw_prev.get("decision", "SKIP")).upper()
                        model_side_prev = str(model_prev.get("decision", "SKIP")).upper()
                        if side_prev in {"LONG", "SHORT"} and side_prev == model_side_prev and not engine.summary()["daily_stop_hit"]:
                            atr = safe_number(model_prev.get("atr14"))
                            entry = float(candle["open"])
                            if atr and atr > 0:
                                stop = entry - atr if side_prev == "LONG" else entry + atr
                                target = entry + 1.5 * atr if side_prev == "LONG" else entry - 1.5 * atr
                                try:
                                    opened = engine.open_position(symbol, side_prev, entry, stop, target, timestamp=candle_time)
                                    logging.info("%s confirmed paper %s opened qty=%.8g", symbol, side_prev, opened["quantity"])
                                except ValueError as exc:
                                    logging.info("%s paper entry blocked: %s", symbol, exc)

                    prefix = candles.iloc[:i + 1].reset_index(drop=True)
                    indicators = add_indicators(prefix)
                    raw_result = assess_candidate(indicators, symbol, expert_pick=True)
                    model_result = assess_regime(prefix, symbol, expert_pick=True)
                    raw_side = str(raw_result.get("decision", "SKIP")).upper()
                    model_side = str(model_result.get("decision", "SKIP")).upper()
                    confirmed = raw_side in {"LONG", "SHORT"} and raw_side == model_side
                    price_row = price_map.get(symbol, {}) if isinstance(price_map, dict) else {}
                    price = safe_number(price_row.get("ls")) or float(candle["close"])
                    journal.record_signal(
                        event_key=f"{symbol}|{candle_time}", timestamp=datetime.now(timezone.utc).isoformat(),
                        symbol=symbol, candle_time=candle_time, price=price, raw_signal=raw_side,
                        model_decision=model_side, confirmed=confirmed,
                        regime=str(model_result.get("regime", "UNKNOWN")),
                        raw_reason=str(raw_result.get("reason", "")),
                        model_reason=str(model_result.get("reason", "")),
                        rsi=safe_number(model_result.get("rsi14")),
                        adx=safe_number(model_result.get("adx14")),
                        atr_pct=safe_number(model_result.get("atr_pct")),
                        relative_volume=safe_number(model_result.get("relative_volume")),
                    )
                    status["processed_candles"] += 1
                logging.info("%s: processed %s new candles", symbol, len(new_indices))
            except Exception as exc:
                logging.exception("Failed processing %s", symbol)
                status["errors"].append(f"{symbol}: {type(exc).__name__}: {exc}")
        status["ok"] = not status["errors"]
        status["message"] = "Completed with errors" if status["errors"] else "Scheduled paper run completed"
    except Exception as exc:
        logging.exception("Public CoinDCX market-data request failed")
        status["ok"] = False
        status["message"] = f"{type(exc).__name__}: {exc}"
        status["errors"].append(status["message"])
    export_dashboard(engine, journal, status)
    logging.info("Dashboard data exported to %s; status=%s", OUTPUT_PATH, status)


if __name__ == "__main__":
    main()
