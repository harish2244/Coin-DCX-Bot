from bot.paper_engine import PersistentPaperEngine
from bot.trade_journal import TradeJournal


def test_signal_journal_records_once_and_summarizes(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    PersistentPaperEngine(db_path=db_path, starting_equity=10_000)
    journal = TradeJournal(db_path)

    kwargs = dict(
        event_key="B-BTC_INR|2026-10-19T01:00:00+00:00",
        timestamp="2026-10-19T01:00:01+00:00",
        symbol="B-BTC_INR",
        candle_time="2026-10-19T01:00:00+00:00",
        price=100_000,
        raw_signal="LONG",
        model_decision="LONG",
        confirmed=True,
        regime="TREND",
        raw_reason="candidate checks passed",
        model_reason="trend filters passed",
        rsi=55,
        adx=25,
        atr_pct=0.2,
        relative_volume=1.2,
    )
    assert journal.record_signal(**kwargs) is True
    assert journal.record_signal(**kwargs) is False

    signals = journal.signals()
    assert len(signals) == 1
    assert bool(signals.iloc[0]["confirmed"]) is True
    summary = journal.summary(10_000)
    assert summary["signals"] == 1
    assert summary["confirmed_signals"] == 1
    assert summary["closed_trades"] == 0


def test_paper_trade_performance_metrics(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    engine = PersistentPaperEngine(
        db_path=db_path, starting_equity=10_000, risk_pct=0.5,
        leverage=3, fee_rate=0, slippage_rate=0,
    )
    journal = TradeJournal(db_path)

    opened = engine.open_position(
        "B-BTC_INR", "LONG", entry=100, stop=99, target=102,
        timestamp="2026-10-19T01:00:00+00:00",
    )
    assert opened["quantity"] > 0
    engine.process_candle(
        "B-BTC_INR", high=103, low=100, close=102.5,
        timestamp="2026-10-19T01:01:00+00:00",
    )
    summary = journal.summary(10_000)
    assert summary["closed_trades"] == 1
    assert summary["wins"] == 1
    assert summary["win_rate_pct"] == 100
    assert summary["net_pnl"] > 0
