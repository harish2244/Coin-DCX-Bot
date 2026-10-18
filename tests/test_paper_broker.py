import pytest
from bot.paper_broker import PaperBroker

def test_long_position_closes_at_target():
    broker = PaperBroker(10_000, 200)
    broker.open_position("BTC/USDT", "LONG", 1, 100, 95, 110, "2026-01-01T00:00:00Z")
    closed = broker.update_price("BTC/USDT", 110)
    assert closed is not None
    assert closed.exit_reason == "TARGET"
    assert closed.pnl == 10
    assert broker.equity == 10_010

def test_only_one_open_position():
    broker = PaperBroker()
    broker.open_position("BTC/USDT", "LONG", 1, 100, 95, 110, "now")
    with pytest.raises(ValueError):
        broker.open_position("ETH/USDT", "LONG", 1, 100, 95, 110, "now")

def test_daily_loss_guard_blocks_new_positions():
    broker = PaperBroker(1000, 50)
    broker.open_position("BTC/USDT", "LONG", 1, 100, 90, 110, "now")
    broker.update_price("BTC/USDT", 40)
    assert broker.daily_stop_hit
    with pytest.raises(ValueError):
        broker.open_position("ETH/USDT", "LONG", 1, 100, 90, 110, "later")
