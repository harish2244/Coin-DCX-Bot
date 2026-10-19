from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_DB = "data/live_paper_trades.sqlite3"


class TradeJournal:
    """Persistent signal/model-decision journal for paper-only monitoring."""

    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS signal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    candle_time TEXT NOT NULL,
                    price REAL,
                    raw_signal TEXT NOT NULL,
                    model_decision TEXT NOT NULL,
                    confirmed INTEGER NOT NULL,
                    regime TEXT,
                    raw_reason TEXT,
                    model_reason TEXT,
                    rsi REAL,
                    adx REAL,
                    atr_pct REAL,
                    relative_volume REAL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_signal_timestamp ON signal_events(timestamp)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_signal_symbol ON signal_events(symbol)")

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=10)

    def record_signal(self, *, event_key: str, timestamp: str, symbol: str,
                      candle_time: str, price: float | None, raw_signal: str,
                      model_decision: str, confirmed: bool, regime: str = "",
                      raw_reason: str = "", model_reason: str = "",
                      rsi: float | None = None, adx: float | None = None,
                      atr_pct: float | None = None,
                      relative_volume: float | None = None) -> bool:
        """Record once per symbol/candle; return False for duplicate events."""
        with self._connect() as db:
            cur = db.execute("""
                INSERT OR IGNORE INTO signal_events
                (event_key,timestamp,symbol,candle_time,price,raw_signal,model_decision,
                 confirmed,regime,raw_reason,model_reason,rsi,adx,atr_pct,relative_volume)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (event_key, timestamp, symbol, candle_time, price, raw_signal,
                  model_decision, int(confirmed), regime, raw_reason, model_reason,
                  rsi, adx, atr_pct, relative_volume))
            return cur.rowcount == 1

    def signals(self, limit: int = 1000) -> pd.DataFrame:
        with self._connect() as db:
            return pd.read_sql_query(
                "SELECT * FROM signal_events ORDER BY timestamp DESC, id DESC LIMIT ?",
                db, params=(int(limit),)
            )

    def trades(self) -> pd.DataFrame:
        with self._connect() as db:
            return pd.read_sql_query(
                "SELECT * FROM paper_trades ORDER BY COALESCE(closed_at, opened_at) DESC, id DESC",
                db
            )

    def summary(self, starting_equity: float = 10000.0) -> dict[str, Any]:
        trades = self.trades()
        signals = self.signals()
        closed = trades[trades["status"] == "CLOSED"].copy() if not trades.empty else trades
        if not closed.empty:
            pnl = pd.to_numeric(closed["net_pnl"], errors="coerce").fillna(0)
            wins = int((pnl > 0).sum())
            losses = int((pnl < 0).sum())
            gross_profit = float(pnl[pnl > 0].sum())
            gross_loss = float(-pnl[pnl < 0].sum())
            net = float(pnl.sum())
            win_rate = wins / len(closed) * 100
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
            expectancy = net / len(closed)
            equity_curve = float(starting_equity) + pnl.iloc[::-1].cumsum()
            peak = equity_curve.cummax()
            drawdown = ((peak - equity_curve) / peak.replace(0, pd.NA) * 100).fillna(0)
            max_drawdown_pct = float(drawdown.max()) if not drawdown.empty else 0.0
        else:
            wins = losses = 0
            gross_profit = gross_loss = net = expectancy = max_drawdown_pct = 0.0
            win_rate = 0.0
            profit_factor = 0.0
        open_count = int((trades["status"] == "OPEN").sum()) if not trades.empty else 0
        confirmed_count = int(signals["confirmed"].sum()) if not signals.empty else 0
        return {
            "signals": len(signals), "confirmed_signals": confirmed_count,
            "closed_trades": len(closed), "open_trades": open_count,
            "wins": wins, "losses": losses, "win_rate_pct": win_rate,
            "gross_profit": gross_profit, "gross_loss": gross_loss,
            "net_pnl": net, "equity_estimate": float(starting_equity) + net,
            "profit_factor": profit_factor, "expectancy": expectancy,
            "max_drawdown_pct": max_drawdown_pct,
        }
