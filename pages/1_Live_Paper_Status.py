from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from bot.coindcx_public import (
    CoinDCXPublicAPIError,
    get_active_futures_instruments,
    get_futures_candles,
    get_futures_current_prices,
)
from bot.indicators import add_indicators, normalize_candles
from bot.paper_engine import PersistentPaperEngine
from bot.regime_strategy import assess_regime
from bot.strategy import assess_candidate
from bot.trade_journal import TradeJournal

st.set_page_config(page_title="CoinDCX Signal & Paper Trading", page_icon="📈", layout="wide")
st.title("CoinDCX Signals · Model Confirmation · Paper Trades")
st.warning("PAPER ONLY — public market data only. This dashboard never submits live CoinDCX orders.")
st.caption("Signal events and simulated trades are stored in SQLite. Keep this app running for continuous monitoring; it is not a hosted always-on service.")

DB_PATH = Path("data/live_paper_trades.sqlite3")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

with st.sidebar:
    st.header("Monitoring settings")
    margin = st.selectbox("Margin currency / accounting", ["INR", "USDT"], index=0)
    equity = st.number_input("Starting paper equity (₹)", min_value=1000.0, value=10000.0, step=1000.0)
    risk_pct = st.number_input("Planned risk per trade (%)", min_value=0.1, max_value=1.0, value=0.5, step=0.1)
    leverage = st.number_input("Notional cap reference (×)", min_value=1.0, max_value=10.0, value=3.0, step=1.0)
    daily_limit = st.number_input("Daily realized loss stop (₹)", min_value=50.0, value=200.0, step=50.0)
    expert_pick = st.checkbox("Pass symbol through Expert Picks gate", value=True)
    auto_paper = st.toggle("Automatically take CONFIRMED paper trades", value=False,
                           help="Only opens simulated trades when the raw signal and regime model agree. No exchange orders are sent.")
    refresh_now = st.button("Refresh market data now")

@st.cache_data(ttl=10, show_spinner=False)
def load_instruments(margin_currency: str) -> list[str]:
    return get_active_futures_instruments(margin_currency)

@st.cache_data(ttl=10, show_spinner=False)
def load_candles(pair: str) -> pd.DataFrame:
    return get_futures_candles(pair, resolution="1", lookback_minutes=240)

@st.cache_data(ttl=10, show_spinner=False)
def load_prices() -> dict:
    return get_futures_current_prices()

if refresh_now:
    st.cache_data.clear()

engine = PersistentPaperEngine(
    db_path=DB_PATH, starting_equity=equity, risk_pct=risk_pct,
    leverage=leverage, daily_loss_limit=daily_limit,
)
journal = TradeJournal(DB_PATH)

try:
    available = load_instruments(margin)
except (CoinDCXPublicAPIError, ValueError, TypeError) as exc:
    available = []
    st.error(f"Could not retrieve active futures instruments: {exc}")

default_candidates = [p for p in available if p in {"B-BTC_INR", "B-ETH_INR", "B-BTC_USDT", "B-ETH_USDT"}]
options = default_candidates + [p for p in available if p not in default_candidates]
if not options:
    st.error("No active instruments were returned by CoinDCX for this margin mode.")
    st.stop()

preferred = "B-BTC_INR" if margin == "INR" else "B-BTC_USDT"
default_index = options.index(preferred) if preferred in options else 0
pair = st.sidebar.selectbox("Futures instrument", options, index=default_index)

if margin == "USDT":
    st.info("USDT contracts can be monitored, but this version's P&L ledger is denominated in ₹. Automatic paper entries are disabled for USDT contracts to avoid mixing currencies.")

@st.fragment(run_every="15s")
def live_monitor():
    now = datetime.now(timezone.utc)
    try:
        raw = load_candles(pair)
        candles = normalize_candles(raw)
        # Only use completed 1-minute candles to avoid changing signals mid-candle.
        current_minute = pd.Timestamp(now).floor("min")
        completed = candles[candles["timestamp"] < current_minute].reset_index(drop=True)
        if len(completed) < 60:
            st.warning(f"Waiting for enough completed candles: {len(completed)}/60.")
            return

        last = completed.iloc[-1]
        candle_time = pd.Timestamp(last["timestamp"]).isoformat()
        prices_payload = load_prices()
        price_map = prices_payload.get("prices", {})
        price_row = price_map.get(pair, {})
        price = float(price_row.get("ls", 0) or 0)
        if price <= 0:
            price = float(last["close"])

        indicator_frame = add_indicators(completed)
        raw_result = assess_candidate(indicator_frame, pair, expert_pick=expert_pick)
        model_result = assess_regime(completed, pair, expert_pick=expert_pick)
        raw_side = str(raw_result.get("decision", "SKIP")).upper()
        model_side = str(model_result.get("decision", "SKIP")).upper()
        confirmed = raw_side in {"LONG", "SHORT"} and raw_side == model_side
        signal_key = f"{pair}|{candle_time}"
        journal.record_signal(
            event_key=signal_key, timestamp=now.isoformat(), symbol=pair,
            candle_time=candle_time, price=price, raw_signal=raw_side,
            model_decision=model_side, confirmed=confirmed,
            regime=str(model_result.get("regime", "UNKNOWN")),
            raw_reason=str(raw_result.get("reason", "")),
            model_reason=str(model_result.get("reason", "")),
            rsi=float(model_result["rsi14"]) if model_result.get("rsi14") is not None else None,
            adx=float(model_result["adx14"]) if model_result.get("adx14") is not None else None,
            atr_pct=float(model_result["atr_pct"]) if model_result.get("atr_pct") is not None else None,
            relative_volume=float(model_result["relative_volume"]) if model_result.get("relative_volume") is not None else None,
        )

        # Process the newly completed candle against any already-open paper position.
        cursor_key = f"paper_processed::{pair}"
        if st.session_state.get(cursor_key) != candle_time:
            opened_before = next((t for t in engine.history() if t["status"] == "OPEN" and t["symbol"] == pair), None)
            if opened_before and margin == "INR":
                closed = engine.process_candle(
                    pair, float(last["high"]), float(last["low"]), float(last["close"]),
                    timestamp=candle_time,
                )
                if closed:
                    st.toast(f"Paper trade closed: {closed['exit_reason']} · net P&L ₹{closed['net_pnl']:,.2f}")
            st.session_state[cursor_key] = candle_time

            summary_before_entry = engine.summary()
            if auto_paper and margin == "INR" and confirmed and not summary_before_entry["has_open_position"] and not summary_before_entry["daily_stop_hit"]:
                atr = float(model_result.get("atr14", 0) or 0)
                if atr > 0:
                    side = raw_side
                    entry = float(last["close"])
                    stop = entry - atr if side == "LONG" else entry + atr
                    target = entry + 1.5 * atr if side == "LONG" else entry - 1.5 * atr
                    try:
                        result = engine.open_position(pair, side, entry, stop, target, timestamp=candle_time)
                        st.toast(f"Confirmed paper {side} opened · quantity {result['quantity']:.8g}")
                    except ValueError as exc:
                        st.info(f"Paper entry blocked by risk controls: {exc}")

        summary = journal.summary(equity)
        trades = journal.trades()
        signals = journal.signals(300)
        history = engine.history()
        open_trade = next((t for t in history if t["status"] == "OPEN" and t["symbol"] == pair), None)
        unrealized = 0.0
        if open_trade and margin == "INR":
            direction = 1 if open_trade["side"] == "LONG" else -1
            unrealized = (price - float(open_trade["entry"])) * float(open_trade["quantity"]) * direction

        st.caption(f"Last refresh: {now.strftime('%Y-%m-%d %H:%M:%S UTC')} · Last completed candle: {candle_time}")
        a, b, c, d = st.columns(4)
        a.metric("CoinDCX price", f"{price:,.8g}")
        b.metric("Raw signal", raw_side)
        c.metric("Model confirmation", "CONFIRMED" if confirmed else "NOT CONFIRMED",
                 f"{raw_side} vs {model_side}")
        d.metric("Market regime", str(model_result.get("regime", "UNKNOWN")))

        st.subheader("Performance dashboard")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Paper equity estimate", f"₹{summary['equity_estimate']:,.2f}")
        p2.metric("Net realized P&L", f"₹{summary['net_pnl']:,.2f}")
        p3.metric("Win rate", f"{summary['win_rate_pct']:.1f}%")
        p4.metric("Profit factor", "∞" if summary["profit_factor"] == float("inf") else f"{summary['profit_factor']:.2f}")
        p5, p6, p7, p8 = st.columns(4)
        p5.metric("Closed trades", str(summary["closed_trades"]))
        p6.metric("Open trades", str(summary["open_trades"]))
        p7.metric("Expectancy / trade", f"₹{summary['expectancy']:,.2f}")
        p8.metric("Max drawdown", f"{summary['max_drawdown_pct']:.2f}%")
        p9, p10, p11, p12 = st.columns(4)
        p9.metric("Signals recorded", str(summary["signals"]))
        p10.metric("Model-confirmed signals", str(summary["confirmed_signals"]))
        p11.metric("Wins / losses", f"{summary['wins']} / {summary['losses']}")
        p12.metric("Unrealized P&L*", f"₹{unrealized:,.2f}")

        st.subheader("Latest model assessment")
        st.write({"raw signal reason": raw_result.get("reason"), "model decision": model_side,
                  "model reason": model_result.get("reason"), "regime": model_result.get("regime"),
                  "RSI": model_result.get("rsi14"), "ADX": model_result.get("adx14"),
                  "ATR %": model_result.get("atr_pct"), "relative volume": model_result.get("relative_volume"),
                  "auto paper entries": auto_paper, "execution": "PAPER ONLY"})

        st.subheader("Open paper position")
        if open_trade:
            st.dataframe(pd.DataFrame([{
                "Symbol": open_trade["symbol"], "Side": open_trade["side"],
                "Quantity": open_trade["quantity"], "Entry": open_trade["entry"],
                "Stop": open_trade["stop"], "Target": open_trade["target"],
                "Opened UTC": open_trade["opened_at"], "Unrealized P&L* (₹)": unrealized,
            }]), use_container_width=True, hide_index=True)
        else:
            st.info("No open paper position for this instrument.")

        tab1, tab2, tab3 = st.tabs(["Signal journal", "Trade ledger", "Equity curve"])
        with tab1:
            if signals.empty:
                st.caption("Signals will appear after the first successful data refresh.")
            else:
                display = signals.drop(columns=["id", "event_key"], errors="ignore")
                st.dataframe(display, use_container_width=True, hide_index=True)
                st.download_button("Download signal journal CSV", display.to_csv(index=False).encode("utf-8"),
                                   "coindcx_signal_journal.csv", "text/csv", key="download_signals")
        with tab2:
            if trades.empty:
                st.caption("No trades recorded yet. Turn on automatic confirmed paper entries to simulate eligible signals.")
            else:
                st.dataframe(trades, use_container_width=True, hide_index=True)
                st.download_button("Download trade ledger CSV", trades.to_csv(index=False).encode("utf-8"),
                                   "coindcx_paper_trades.csv", "text/csv", key="download_trades")
        with tab3:
            if not trades.empty and "status" in trades.columns:
                closed = trades[trades["status"] == "CLOSED"].copy()
                if not closed.empty:
                    closed["closed_at"] = pd.to_datetime(closed["closed_at"], errors="coerce", utc=True)
                    closed = closed.sort_values("closed_at")
                    closed["net_pnl"] = pd.to_numeric(closed["net_pnl"], errors="coerce").fillna(0)
                    closed["equity"] = equity + closed["net_pnl"].cumsum()
                    st.line_chart(closed.set_index("closed_at")["equity"])
                else:
                    st.caption("Equity curve will appear after the first closed trade.")

        st.caption("All P&L is simulated, before funding and exchange-specific contract rules. Unrealized P&L excludes exit costs. Planned risk is not a guaranteed maximum loss.")
    except (CoinDCXPublicAPIError, ValueError, TypeError, KeyError) as exc:
        st.error(f"Dashboard refresh failed: {exc}")
        st.caption("No live order was attempted. Check the public market-data endpoint and instrument selection.")

live_monitor()
