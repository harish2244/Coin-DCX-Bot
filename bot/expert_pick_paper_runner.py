from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from bot.coindcx_public import get_active_futures_instruments, get_futures_candles, get_futures_current_prices
from bot.expert_pick_strategy_v2 import evaluate_expert_pick

DB = Path("data/expert_pick_paper.sqlite3")
OUT = Path("docs/data/expert-pick-paper-trades.json")
PICKS_FILE = Path("docs/data/expert-picks.json")
STARTING_EQUITY_INR = 10_000.0
RISK_INR = 50.0
DAILY_STOP_INR = 200.0
LEVERAGE_CAP = 3.0
FX_ESTIMATE = 88.0
FEE_RATE = 0.0005
SLIPPAGE_RATE = 0.0005
MIN_RR = 1.1
# Exact candidate mappings only. A symbol is activated only if it appears in the public active-instruments feed.
PICKS = [
    {"id":"sol-2026-10-19","instrument":"SOL","side":"LONG","low":104.46,"high":105.51,"stop":103.68,"target":106.69,"valid":"2026-10-23","symbol_terms":["B-SOL_USDT"]},
    {"id":"nikkei-2026-10-15","instrument":"Nikkei225","side":"LONG","low":63450,"high":64400,"stop":62200,"target":66168,"valid":"2026-10-19","symbol_terms":["B-NIKKEI225_USDC","B-NIKKEI_USDC","B-JP225_USDC"]},
    {"id":"copper-2026-10-19","instrument":"COPPER","side":"LONG","low":6.475,"high":6.54,"stop":6.385,"target":6.666,"valid":"2026-10-20","symbol_terms":["B-COPPER_USDT"]},
    {"id":"cl-2026-10-19","instrument":"CL","side":"SHORT","low":96.99,"high":97.91,"stop":98.62,"target":95.92,"valid":"2026-10-23","symbol_terms":["B-CL_USDT"]},
    {"id":"xau-2026-10-18","instrument":"XAU","side":"SHORT","low":4181,"high":4244,"stop":4291,"target":4110.45,"valid":"2026-10-23","symbol_terms":["B-XAU_USDT"]},
]
# Heartbeat marker: keep this worker file on the monitored workflow path.
def now_utc(): return datetime.now(timezone.utc)
def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db=sqlite3.connect(DB, timeout=10)
    db.row_factory=sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS expert_trades (
      id INTEGER PRIMARY KEY AUTOINCREMENT, pick_id TEXT NOT NULL, instrument TEXT NOT NULL,
      symbol TEXT, side TEXT NOT NULL, entry REAL, stop REAL, target REAL, quantity REAL,
      opened_at TEXT, status TEXT NOT NULL, exit_price REAL, exit_reason TEXT,
      gross_pnl_inr REAL, costs_inr REAL, net_pnl_inr REAL, closed_at TEXT,
      note TEXT, UNIQUE(pick_id, opened_at))""")
    db.execute("""CREATE TABLE IF NOT EXISTS expert_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    db.commit()
    return db
def rr(p, price):
    risk=abs(price-p["stop"]); reward=abs(p["target"]-price)
    return reward/risk if risk else 0
def main():
    now=now_utc(); today=now.date().isoformat()
    db=connect()
    active=[]
    warnings=[]
    for margin in ("USDT","USDC","INR"):
        try: active.extend(get_active_futures_instruments(margin))
        except Exception as e: warnings.append(f"{margin} instruments: {type(e).__name__}: {e}")
    active=set(active)
    prices={}
    try:
        raw=get_futures_current_prices().get("prices",{})
        if isinstance(raw,dict): prices=raw
    except Exception as e: warnings.append(f"price snapshot: {type(e).__name__}: {e}")
    rows=[]
    model_evaluations=0
    directional_signals=0
    pick_direction_matches=0
    candle_data_ok=0
    for p in PICKS:
        model_regime="UNKNOWN"
        model_reason="Model not evaluated"
        model_metrics={}
        matching=[s for s in p["symbol_terms"] if s in active]
        symbol=matching[0] if len(matching)==1 else None
        price_row=prices.get(symbol,{}) if symbol else {}
        price=None
        if isinstance(price_row,dict):
            for k in ("ls","last_price","p","price","cp"):
                try:
                    if price_row.get(k) is not None: price=float(price_row[k]); break
                except (TypeError,ValueError): pass
        if not symbol:
            state="UNMAPPED_MARKET"
            note="No exact market ID verified in active instruments; no paper order opened."
        elif now.date().isoformat()>p["valid"]:
            state="EXPIRED"
            note="Expert Pick validity expired; no new entries."
        elif price is None:
            state="WAITING_FOR_PRICE"
            note="Market mapped, but current price field was unavailable."
        else:
            state="WAITING_FOR_ENTRY"
            note="Waiting for price inside entry range and model confirmation."
        if symbol and price is not None and now.date().isoformat()<=p["valid"]:
            try:
                # Fetch enough completed 5m bars to construct a meaningful 15m confirmation series.
                candles=get_futures_candles(symbol,resolution="5",lookback_minutes=1800)
                candles=candles[candles["timestamp"] + pd.Timedelta(minutes=5) <= pd.Timestamp(now).floor("min")].reset_index(drop=True)
                candles_15m=(candles.set_index("timestamp")
                    .resample("15min", origin="start_day", label="left", closed="left")
                    .agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"})
                    .dropna().reset_index())
                candles_15m=candles_15m[candles_15m["timestamp"] + pd.Timedelta(minutes=15) <= pd.Timestamp(now).floor("min")].reset_index(drop=True)
                if len(candles)>=70 and len(candles_15m)>=70:
                    v2_pick={"side":p["side"],"low":p["low"],"high":p["high"],"stop":p["stop"],"target":p["target"],"valid":p["valid"]}
                    v2=evaluate_expert_pick(v2_pick,price,candles,candles_15m,today=now.date())
                    raw_decision=str(v2.get("decision","WAIT")).upper()
                    model_decision=p["side"] if raw_decision=="ENTER_CANDIDATE" else "SKIP"
                    model_regime=f"V2_{raw_decision}"
                    model_reason=str(v2.get("reason","No reason returned"))
                    model_metrics={k:v2.get(k) for k in ("model_5m","regime_5m","model_15m","regime_15m","net_reward_risk_estimate","price_in_range") if v2.get(k) is not None}
                    model_evaluations += 1
                    candle_data_ok += 1
                    if raw_decision=="ENTER_CANDIDATE":
                        directional_signals += 1
                        pick_direction_matches += 1
                    touched=p["low"]<=price<=p["high"]
                    correct_model=raw_decision=="ENTER_CANDIDATE"
                    risk=abs(price-p["stop"])
                    ratio=float(v2.get("net_reward_risk_estimate",0.0))
                    db.execute("UPDATE expert_trades SET status='CLOSED',exit_price=?,exit_reason='STOP',closed_at=?,note='Stop triggered by observed candle' WHERE status='OPEN' AND symbol=? AND side='LONG' AND stop>=?",
                               (p["stop"],now.isoformat(),symbol,price)) if False else None
                    # Manage open position from the latest completed candle, stop first.
                    open_row=db.execute("SELECT * FROM expert_trades WHERE status='OPEN' LIMIT 1").fetchone()
                    if open_row and open_row["symbol"] == symbol:
                        last=candles.iloc[-1]
                        hi,lo=float(last.high),float(last.low)
                        reason=("STOP" if (lo<=open_row["stop"] if open_row["side"]=="LONG" else hi>=open_row["stop"]) else
                                ("TARGET" if (hi>=open_row["target"] if open_row["side"]=="LONG" else lo<=open_row["target"]) else None))
                        if reason:
                            exit_raw=open_row["stop"] if reason=="STOP" else open_row["target"]
                            direction=1 if open_row["side"]=="LONG" else -1
                            entry=open_row["entry"]; qty=open_row["quantity"]
                            exit_price=exit_raw*(1-SLIPPAGE_RATE if direction==1 else 1+SLIPPAGE_RATE)
                            entry_eff=entry*(1+SLIPPAGE_RATE if direction==1 else 1-SLIPPAGE_RATE)
                            gross=(exit_price-entry_eff)*qty*direction*FX_ESTIMATE
                            costs=(entry_eff*qty+exit_price*qty)*FEE_RATE*FX_ESTIMATE
                            db.execute("UPDATE expert_trades SET status='CLOSED',exit_price=?,exit_reason=?,closed_at=?,gross_pnl_inr=?,costs_inr=?,net_pnl_inr=?,note=? WHERE id=?",
                              (exit_price,reason,now.isoformat(),gross,costs,gross-costs,"Simulated candle exit; stablecoin conversion is estimated",open_row["id"]))
                    daily=db.execute("SELECT COALESCE(SUM(net_pnl_inr),0) FROM expert_trades WHERE status='CLOSED' AND substr(closed_at,1,10)=?",(today,)).fetchone()[0]
                    open_row=db.execute("SELECT * FROM expert_trades WHERE status='OPEN' LIMIT 1").fetchone()
                    if open_row:
                        state="POSITION_OPEN" if open_row["pick_id"]==p["id"] else "BLOCKED_ONE_POSITION"
                        note=f"Shared account position {open_row['instrument']} is open; one-position rule."
                    elif daily<=-DAILY_STOP_INR:
                        state="DAILY_STOP"
                        note="Daily realized loss stop reached; no new paper entries."
                    elif not touched:
                        state="WAITING_FOR_ENTRY"
                        note=f"Live price {price:g} outside entry range."
                    elif not correct_model:
                        state="WAITING_FOR_MODEL"
                        note=f"Price in range, but v2 says {raw_decision}: {model_reason}; no entry."
                    elif risk<=0 or ratio<MIN_RR:
                        state="REJECTED_RISK_REWARD"
                        note=f"Entry blocked: reward/risk {ratio:.2f} below minimum {MIN_RR:.2f}."
                    else:
                        equity=STARTING_EQUITY_INR+float(db.execute("SELECT COALESCE(SUM(net_pnl_inr),0) FROM expert_trades WHERE status='CLOSED'").fetchone()[0])
                        risk_cash=min(RISK_INR,max(0,equity)*0.005)
                        qty_risk=(risk_cash/FX_ESTIMATE)/risk
                        qty_leverage=(equity/FX_ESTIMATE*LEVERAGE_CAP)/price
                        qty=min(qty_risk,qty_leverage)
                        db.execute("""INSERT OR IGNORE INTO expert_trades
                          (pick_id,instrument,symbol,side,entry,stop,target,quantity,opened_at,status,note)
                          VALUES(?,?,?,?,?,?,?,?,?,'OPEN',?)""",
                          (p["id"],p["instrument"],symbol,p["side"],price,p["stop"],p["target"],qty,now.isoformat(),
                           "Paper entry at observed live price; 3x cap and 0.5% risk sizing."))
                        state="PAPER_OPEN" if db.execute("SELECT changes()").fetchone()[0] else "POSITION_ALREADY_RECORDED"
                        note="Paper position opened at observed live price; no live order placed."
                else:
                    model_decision="SKIP"; state="INSUFFICIENT_CANDLES"; note="Insufficient completed candles for model confirmation."
            except Exception as e:
                model_decision="UNKNOWN"; state="DATA_ERROR"; note=f"{type(e).__name__}: {e}"
        else:
            model_decision="UNKNOWN"
        openrow=db.execute("SELECT * FROM expert_trades WHERE pick_id=? ORDER BY id DESC LIMIT 1",(p["id"],)).fetchone()
        rows.append({"pick_id":p["id"],"instrument":p["instrument"],"symbol":symbol,"side":p["side"],
          "entry_range":f'{p["low"]}–{p["high"]}',"live_price":price,"stop":p["stop"],"target":p["target"],
          "valid_until":p["valid"],"model_decision":model_decision,"model_regime":model_regime,
          "model_reason":model_reason,"model_metrics":model_metrics,
          "pick_direction":p["side"],"price_in_entry_range":bool(price is not None and p["low"]<=price<=p["high"]),
          "paper_status":state,"signal_stage":state,"entry_checks":{"signal_detected":model_decision in {"LONG","SHORT"},"direction_matches":model_decision==p["side"],"price_in_range":bool(price is not None and p["low"]<=price<=p["high"]),"paper_opened":state=="PAPER_OPEN"},
          "price_data_status":"OK" if price is not None else ("UNMAPPED" if not symbol else "NO_PRICE"),
          "candle_data_status":"OK" if model_decision != "UNKNOWN" else ("ERROR_OR_NOT_CHECKED" if symbol else "UNMAPPED"),
          "signal_detected":model_decision in {"LONG","SHORT"},
          "signal_matches_pick":model_decision == p["side"],
          "entry":openrow["entry"] if openrow else None,"opened_at":openrow["opened_at"] if openrow else None,
          "exit_price":openrow["exit_price"] if openrow else None,"exit_reason":openrow["exit_reason"] if openrow else None,
          "net_pnl_inr":openrow["net_pnl_inr"] if openrow else None,"note":note})
    db.commit()
    trades=[dict(r) for r in db.execute("SELECT * FROM expert_trades ORDER BY id DESC").fetchall()]
    total_net=float(db.execute("SELECT COALESCE(SUM(net_pnl_inr),0) FROM expert_trades WHERE status='CLOSED'").fetchone()[0])
    payload={"updated_at":now.isoformat(),"mode":"PAPER ONLY — no live orders","starting_equity_inr":STARTING_EQUITY_INR,
      "risk_per_trade_inr":RISK_INR,"max_leverage":LEVERAGE_CAP,"daily_loss_stop_inr":DAILY_STOP_INR,
      "minimum_reward_risk":MIN_RR,"strategy_version":"expert_pick_v2","execution_mode":"paper_only",
      "health":{"runner_completed":True,"prices_available":sum(1 for x in rows if x.get("live_price") is not None),
        "markets_mapped":sum(1 for x in rows if x.get("symbol")),"candle_data_ok":candle_data_ok,
        "model_evaluations":model_evaluations,"directional_signals":directional_signals,
        "signals_matching_pick":pick_direction_matches,"paper_entries_total":sum(1 for t in trades if t.get("opened_at")),
        "last_run_at":now.isoformat(),"scheduled_interval_minutes":10},
      "net_realized_pnl_inr":round(total_net,2),
      "equity_estimate_inr":round(STARTING_EQUITY_INR+total_net,2),"warnings":warnings,"picks":rows,"trades":trades}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,default=str))
    print(json.dumps(payload,indent=2))
    db.close()
if __name__=="__main__": main()
