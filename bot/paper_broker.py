from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass
class PaperPosition:
    symbol: str
    side: str
    quantity: float
    entry: float
    stop: float
    target: float
    opened_at: str
    status: str = "OPEN"
    exit_price: float | None = None
    pnl: float | None = None
    exit_reason: str | None = None

class PaperBroker:
    """In-memory simulation only; state resets when the app session restarts."""
    def __init__(self, starting_equity: float = 10000, daily_loss_limit: float = 200):
        self.starting_equity = float(starting_equity)
        self.equity = float(starting_equity)
        self.daily_loss_limit = float(daily_loss_limit)
        self.realized_pnl = 0.0
        self.positions: list[PaperPosition] = []

    @property
    def has_open_position(self):
        return any(p.status == "OPEN" for p in self.positions)

    @property
    def daily_stop_hit(self):
        return self.realized_pnl <= -self.daily_loss_limit

    def open_position(self, symbol, side, quantity, entry, stop, target, opened_at):
        if self.daily_stop_hit:
            raise ValueError("Daily loss limit reached.")
        if self.has_open_position:
            raise ValueError("Only one open paper position is allowed.")
        side = side.upper()
        if side not in {"LONG", "SHORT"}:
            raise ValueError("Side must be LONG or SHORT.")
        if min(quantity, entry, stop, target) <= 0:
            raise ValueError("Prices and quantity must be positive.")
        if side == "LONG" and not stop < entry < target:
            raise ValueError("LONG requires stop < entry < target.")
        if side == "SHORT" and not target < entry < stop:
            raise ValueError("SHORT requires target < entry < stop.")
        p = PaperPosition(symbol, side, quantity, entry, stop, target, opened_at)
        self.positions.append(p)
        return p

    def update_price(self, symbol, price):
        if price <= 0:
            raise ValueError("Price must be positive.")
        for p in self.positions:
            if p.status != "OPEN" or p.symbol != symbol:
                continue
            reason = None
            if p.side == "LONG":
                reason = "STOP" if price <= p.stop else ("TARGET" if price >= p.target else None)
            else:
                reason = "STOP" if price >= p.stop else ("TARGET" if price <= p.target else None)
            if reason:
                p.exit_price = float(price)
                p.pnl = (price - p.entry) * p.quantity * (1 if p.side == "LONG" else -1)
                p.exit_reason, p.status = reason, "CLOSED"
                self.realized_pnl += p.pnl
                self.equity += p.pnl
                return p
        return None

    def history(self):
        return [asdict(p) for p in self.positions]
