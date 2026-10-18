from bot.paper_engine import PersistentPaperEngine


def test_persistent_engine_survives_reopen_and_closes_stop_first(tmp_path):
    path = tmp_path / "paper.sqlite3"
    engine = PersistentPaperEngine(path, starting_equity=10_000, risk_pct=0.5)
    opened = engine.open_position("B-BTC_USDT", "LONG", 100, 99, 102, "2026-10-18T10:00:00+00:00")
    assert opened["planned_risk_before_costs"] <= 50.01
    # Candle touches both stop and target: conservative stop-first rule.
    closed = engine.process_candle("B-BTC_USDT", high=103, low=98, close=101,
                                   timestamp="2026-10-18T10:01:00+00:00")
    assert closed["exit_reason"] == "STOP"
    assert closed["net_pnl"] < 0
    reopened = PersistentPaperEngine(path, starting_equity=10_000)
    assert len(reopened.history()) == 1
    assert reopened.history()[0]["status"] == "CLOSED"


def test_one_open_position_only(tmp_path):
    engine = PersistentPaperEngine(tmp_path / "paper.sqlite3")
    engine.open_position("B-BTC_USDT", "LONG", 100, 99, 102)
    try:
        engine.open_position("B-ETH_USDT", "SHORT", 100, 101, 98)
    except ValueError as exc:
        assert "Only one" in str(exc)
    else:
        raise AssertionError("second open position should be rejected")


def test_candle_validation(tmp_path):
    engine = PersistentPaperEngine(tmp_path / "paper.sqlite3")
    try:
        engine.process_candle("B-BTC_USDT", high=90, low=100, close=95)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid candle should be rejected")
