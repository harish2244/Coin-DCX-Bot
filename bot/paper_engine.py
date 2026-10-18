from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = "paper_trades.sqlite3"


class PersistentPaperEngine:
    """SQLite-backed paper ledger. Never connects to authenticated/trading endpoints."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB,
        starting_equity: float = 10_000.0,
        risk_pct: float = 0.5,
        leverage: float = 3.0,
        daily_loss_limit: float = 200.0,
        fee_rate: float = 0.0005,
        slippage_rate: float = 0.0005,
    ):
        if starting_equity <= 0 or not 0 < risk_pct <= 1 or leverage <= 0:
            raise ValueError("Invalid equity, risk percentage, or leverage")
        if daily_loss_limit <= 0 or fee_rate < 0 or slippage_rate < 0:
            raise ValueError("Invalid loss limit or trading-cost rates")
        self.db_path = str(db_path)
        self.starting_equity = float(starting_equity)
        self.risk_pct = float(risk_pct)
        self.leverage = float(leverage)
        self.daily_loss_limit = float(daily_loss_limit)
        self.fee_rate = float(fee_rate)
        self.slippage_rate = float(slippage_rate)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL, side TEXT NOT NULL,
                    quantity REAL NOT NULL, entry REAL NOT NULL,
                    stop REAL NOT NULL, target REAL NOT NULL,
                    opened_at TEXT NOT NULL, status TEXT NOT NULL,
                    exit_price REAL, exit_reason TEXT, gross_pnl REAL,
                    costs REAL, net_pnl REAL, closed_at TEXT
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS paper_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                )
            """)
            db.execute(
                "INSERT OR IGNORE INTO paper_meta(key,value) VALUES('starting_equity',?)",
                (str(self.starting_equity),),
            )

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=10)

    def _day_start(self) -> str:
        return datetime.now(timezone.utc).date().isoformat() + "T00:00:00+00:00"

    def _realized_since_day_start(self, db) -> float:
        row = db.execute(
            "SELECT COALESCE(SUM(net_pnl),0) FROM paper_trades WHERE status='CLOSED' AND closed_at >= ?",
            (self._day_start(),),
        ).fetchone()
        return float(row[0])

    def summary(self) -> dict[str, Any]:
        with self._connect() as db:
            pnl = float(db.execute("SELECT COALESCE(SUM(net_pnl),0) FROM paper_trades WHERE status='CLOSED'").fetchone()[0])
            daily = self._realized_since_day_start(db)
            opened = db.execute("SELECT COUNT(*) FROM paper_trades WHERE status='OPEN'").fetchone()[0]
            return {
                "starting_equity": self.starting_equity,
                "equity_estimate": self.starting_equity + pnl,
                "realized_net_pnl": pnl,
                "daily_net_pnl": daily,
                "daily_stop_hit": daily <= -self.daily_loss_limit,
                "has_open_position": opened > 0,
                "daily_loss_limit": self.daily_loss_limit,
            }

    def open_position(self, symbol: str, side: str, entry: float, stop: float, target: float,
                      timestamp: str | None = None) -> dict[str, Any]:
        side = side.upper()
        if not symbol or side not in {"LONG", "SHORT"}:
            raise ValueError("Symbol and LONG/SHORT side are required")
        if min(entry, stop, target) <= 0:
            raise ValueError("Entry, stop, and target must be positive")
        if side == "LONG" and not stop < entry < target:
            raise ValueError("LONG requires stop < entry < target")
        if side == "SHORT" and not target < entry < stop:
            raise ValueError("SHORT requires target < entry < stop")
        with self._connect() as db:
            if self._realized_since_day_start(db) <= -self.daily_loss_limit:
                raise ValueError("Daily loss stop reached; paper entries disabled")
            if db.execute("SELECT 1 FROM paper_trades WHERE status='OPEN' LIMIT 1").fetchone():
                raise ValueError("Only one open paper position is allowed")
            risk_cash = self.starting_equity * self.risk_pct / 100.0
            qty = risk_cash / abs(entry - stop)
            max_notional = self.starting_equity * self.leverage
            qty = min(qty, max_notional / entry)
            opened_at = timestamp or datetime.now(timezone.utc).isoformat()
            cur = db.execute(
                """INSERT INTO paper_trades
                (symbol,side,quantity,entry,stop,target,opened_at,status)
                VALUES(?,?,?,?,?,?,?,'OPEN')""",
                (symbol, side, qty, entry, stop, target, opened_at),
            )
            trade_id = int(cur.lastrowid)
        return {"id": trade_id, "symbol": symbol, "side": side, "quantity": qty,
                "entry": entry, "stop": stop, "target": target,
                "planned_risk_before_costs": qty * abs(entry - stop)}

    def process_candle(self, symbol: str, high: float, low: float, close: float,
                       timestamp: str | None = None) -> dict[str, Any] | None:
        """Use candle high/low to test stop/target. If both touched, conservatively assume stop first."""
        if min(high, low, close) <= 0 or high < low:
            raise ValueError("Invalid candle prices")
        with self._connect() as db:
            row = db.execute(
                "SELECT id,side,quantity,entry,stop,target FROM paper_trades WHERE symbol=? AND status='OPEN'",
                (symbol,),
            ).fetchone()
            if not row:
                return None
            trade_id, side, qty, entry, stop, target = row
            if side == "LONG":
                reason = "STOP" if low <= stop else ("TARGET" if high >= target else None)
            else:
                reason = "STOP" if high >= stop else ("TARGET" if low <= target else None)
            if reason is None:
                return None
            raw_exit = stop if reason == "STOP" else target
            exit_price = raw_exit * (1 - self.slippage_rate if side == "LONG" else 1 + self.slippage_rate)
            entry_cost = entry * qty * self.fee_rate
            exit_cost = exit_price * qty * self.fee_rate
            gross = (exit_price - entry) * qty * (1 if side == "LONG" else -1)
            costs = entry_cost + exit_cost
            net = gross - costs
            closed_at = timestamp or datetime.now(timezone.utc).isoformat()
            db.execute(
                """UPDATE paper_trades SET status='CLOSED',exit_price=?,exit_reason=?,
                gross_pnl=?,costs=?,net_pnl=?,closed_at=? WHERE id=?""",
                (exit_price, reason, gross, costs, net, closed_at, trade_id),
            )
            return {"id": trade_id, "symbol": symbol, "side": side, "exit_price": exit_price,
                    "exit_reason": reason, "gross_pnl": gross, "estimated_costs": costs,
                    "net_pnl": net}

    def history(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM paper_trades ORDER BY id DESC").fetchall()
            return [dict(row) for row in rows]
