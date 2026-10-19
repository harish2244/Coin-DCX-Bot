from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from bot.coindcx_public import get_active_futures_instruments, get_futures_candles
from bot.regime_strategy import assess_regime

OUT = Path("docs/data/expert-pick-replay.json")
START = pd.Timestamp("2026-10-15T00:00:00Z")
PICKS = [
 {"instrument":"Nikkei225","direction":"LONG","low":63450.0,"high":64400.0,"stop":62200.0,"target":66168.0,"valid":"2026-10-19","terms":["NIKKEI","JPN225","JP225","NI225"]},
 {"instrument":"XAU","direction":"SHORT","low":4181.0,"high":4244.0,"stop":4291.0,"target":4110.45,"valid":"2026-10-23","terms":["XAU","GOLD","XAUUSD"]},
]

def main():
 out={"generated_at":datetime.now(timezone.utc).isoformat(),"mode":"PAPER_ONLY","method":"1-minute historical replay; decision uses candles through close; entry at close; exit tested on subsequent candles; stop assumed first if stop and target touch in same candle.","picks":[]}
 try:
  active=sorted(set(get_active_futures_instruments("USDT")+get_active_futures_instruments("INR")))
 except Exception as e:
  active=[]; out["market_list_error"]=str(e)
 for p in PICKS:
  matches=[s for s in active if any(t in s.upper() for t in p["terms"])]
  if len(matches)!=1:
   out["picks"].append({"instrument":p["instrument"],"status":"BLOCKED_UNVERIFIED_MARKET_ID","candidate_matches":matches,"message":"No unique pair match; no counts fabricated."}); continue
  pair=matches[0]
  try:
   df=get_futures_candles(pair,resolution="1",lookback_minutes=10000)
   df=df[df.timestamp>=START].copy().reset_index(drop=True)
   end=pd.Timestamp(p["valid"]+"T23:59:59Z")
   df=df[df.timestamp<=end].reset_index(drop=True)
   signals=[]; entries=[]; exits=[]; pos=None; range_touches=0; raw=0; confirmed=0
   for i in range(60,len(df)):
    row=df.iloc[i]
    touch=float(row.low)<=p["high"] and float(row.high)>=p["low"]
    if touch: range_touches+=1
    model=assess_regime(df.iloc[:i+1],pair,expert_pick=True)
    side=str(model.get("decision","SKIP")).upper()
    if side in ("LONG","SHORT"): raw+=1
    ok=touch and side==p["direction"]
    if ok:
     confirmed+=1
     signals.append({"time":row.timestamp.isoformat(),"close":float(row.close),"decision":side,"regime":model.get("regime"),"reason":model.get("reason")})
    if pos is None and ok:
     price=float(row.close)
     geometry=(p["stop"]<price<p["target"]) if side=="LONG" else (p["target"]<price<p["stop"])
     if geometry:
      pos={"entry_time":row.timestamp.isoformat(),"entry":price,"side":side,"stop":p["stop"],"target":p["target"]}
      entries.append(dict(pos))
     continue
    if pos:
     why=("STOP" if float(row.low)<=pos["stop"] else ("TARGET" if float(row.high)>=pos["target"] else None)) if pos["side"]=="LONG" else ("STOP" if float(row.high)>=pos["stop"] else ("TARGET" if float(row.low)<=pos["target"] else None))
     if why:
      px=pos["stop"] if why=="STOP" else pos["target"]
      exits.append({**pos,"exit_time":row.timestamp.isoformat(),"exit_price":px,"exit_reason":why})
      pos=None
   if pos: exits.append({**pos,"exit_time":None,"exit_price":None,"exit_reason":"OPEN_AT_END"})
   out["picks"].append({"instrument":p["instrument"],"pair":pair,"status":"REPLAYED","window_start":str(START),"window_end":end.isoformat(),"candles_checked":len(df),"range_touch_candles":range_touches,"raw_directional_signals":raw,"range_touch_plus_model_confirmations":confirmed,"entry_count":len(entries),"entries":entries,"exits":exits,"closed_exit_count":sum(e["exit_reason"]!="OPEN_AT_END" for e in exits),"open_at_end":sum(e["exit_reason"]=="OPEN_AT_END" for e in exits),"confirmed_signals":signals})
  except Exception as e:
   out["picks"].append({"instrument":p["instrument"],"pair":pair,"status":"DATA_ERROR","message":str(e)})
 out["overall_status"]="COMPLETE" if out["picks"] and all(p.get("status")=="REPLAYED" for p in out["picks"]) else "PARTIAL_OR_BLOCKED"
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,indent=2))
 print(json.dumps(out,indent=2))
if __name__=="__main__": main()
