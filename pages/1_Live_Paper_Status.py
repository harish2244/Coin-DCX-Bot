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
from bot.indicators import normalize_candles
from bot.paper_engine import PersistentPaperEngine
from bot.regime_strategy import assess_regime

st.set_page_config(page_title="Live Paper Status | CoinDCX", page_icon="📈", layout="wide")
st.title("Live Paper Trading Status")
st.warning("PAPER ONLY — public CoinDCX market data. No authenticated endpoints or live orders are used.")
st.caption("This page refreshes about every 15 seconds while open. Entries are simulated only from completed candles.")

with st.sidebar:
    st.header("Paper configuration")
    equity = st.number_input("Starting paper equity (₹)", min_value=1000.0, value=10000.0, step=1000.0)
    risk_pct = st.number_input("Planned risk per trade (%)", min_value=0.1, max_value=1.0, value=0.5, step=0.1)
    leverage = st.number_input("Notional cap reference (×)", min_value=1.0, max_value=10.0, value=3.0, step=1.0)
    daily_limit = st.number_input("Daily realized loss stop (₹)", min_value=50.0, value=200.0, step=50.0)
    margin = st.selectbox("Instrument margin currency", ["INR", "USDT"], index=0)
    pair_filter = st.selectbox("Instrument", ["B-BTC_INR", "B-ETH_INR", "B-BTC_USDT", "B-ETH_USDT"])
    auto_paper = st.toggle("Enable automatic PAPER entries", value=False,
                           help="When enabled, eligible signals may open simulated positions. This never sends exchange orders.")
    st.caption("For INR-denominated accounting, use an INR-margined instrument. USDT pairs are shown for monitoring only in this first version.")

DB_PATH = Path("data/live_paper_trades.sqlite3")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = PersistentPaperEngine(
    db_path=DB_PATH,
    starting_equity=equity,
    risk_pct=risk_pct,
    leverage=leverage,
    daily_loss_limit=daily_limit,
)

@st.cache_data(ttl=10, show_spinner=False)
def load_instruments(margin_currency: str) -> list[str]:
    return get_active_futures_instruments(margin_currency)

@st.cache_data(ttl=10, show_spinner=False)
def load_candles(pair: str) -> pd.DataFrame:
    return get_futures_candles(pair, resolution="1", lookback_minutes=240)

@st.cache_data(ttl=10, show_spinner=False)
def load_prices() -> dict:
    return get_futures_current_prices()

@st.fragment(run_every="15s")
def live_panel():
    now = datetime.now(timezone.utc)
    st.caption(f"Last page refresh: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    try:
        instruments = load_instruments(margin)
        if pair_filter not in instruments:
            st.error(f"{pair_filter} is not currently listed as an active {margin}-margined instrument. Select a listed instrument from the available list below.")
            st.write("Available instruments (sample):", instruments[:30])
            return

        raw = load_candles(pair_filter)
        frame = normalize_candles(raw)
        # Do not make decisions on a still-forming 1-minute candle.
        current_minute = pd.Timestamp(now).floor("min")
        completed = frame[frame["timestamp"] < current_minute].reset_index(drop=True)
        if len(completed) < 60:
            st.error(f"Only {len(completed)} completed candles are available; at least 60 are required.")
            return

        last_candle = completed.iloc[-1]
        candle_key = str(last_candle["timestamp"])
        prices_payload = load_prices()
        prices = prices_payload.get("prices", {})
        price_row = prices.get(pair_filter, {})
        last_price = float(price_row.get("ls", 0) or 0)
        if last_price <= 0:
            last_price = float(last_candle["close"])

        summary = engine.summary()
        history = engine.history()
        open_trade = next((t for t in history if t["status"] == "OPEN"), None)

        # Process each completed candle once per running app session.
        cursor_key = f"processed_candle::{pair_filter}"
        closed_this_cycle = False
        if st.session_state.get(cursor_key) != candle_key:
            if open_trade and margin == "INR":
                closed = engine.process_candle(
                    pair_filter, float(last_candle["high"]), float(last_candle["low"]),
                    float(last_candle["close"]), timestamp=last_candle["timestamp"].isoformat()
                )
                closed_this_cycle = closed is not None
                if closed:
                    st.warning(f"Paper position closed at {closed['exit_reason']}; estimated net P&L ₹{closed['net_pnl']:,.2f}.")
            st.session_state[cursor_key] = candle_key

            signal = assess_regime(completed, pair_filter, expert_pick=True)
            st.session_state[f"signal::{pair_filter}"] = signal
            st.session_state[f"signal_candle::{pair_filter}"] = candle_key

            if auto_paper and margin == "INR" and not closed_this_cycle:
                summary = engine.summary()
                if signal.get("decision") in {"LONG", "SHORT"} and not summary["has_open_position"] and not summary["daily_stop_hit"]:
                    entry = float(last_candle["close"])
                    atr = float(signal.get("atr14", 0) or 0)
                    if atr > 0:
                        side = signal["decision"]
                        stop = entry - atr if side == "LONG" else entry + atr
                        target = entry + 1.5 * atr if side == "LONG" else entry - 1.5 * atr
                        try:
                            opened = engine.open_position(
                                pair_filter, side, entry, stop, target,
                                timestamp=last_candle["timestamp"].isoformat()
                            )
                            st.success(f"Paper {side} opened for {pair_filter}; simulated quantity {opened['quantity']:.8g}.")
                        except ValueError as exc:
                            st.info(f"No paper entry: {exc}")

        signal = st.session_state.get(f"signal::{pair_filter}", {"decision": "SKIP", "regime": "UNKNOWN", "reason": "Waiting for first completed candle"})
        summary = engine.summary()
        history = engine.history()
        open_trade = next((t for t in history if t["status"] == "OPEN"), None)
        mtm = 0.0
        if open_trade and margin == "INR":
            direction = 1 if open_trade["side"] == "LONG" else -1
            mtm = (last_price - float(open_trade["entry"])) * float(open_trade["quantity"]) * direction

        a, b, c, d = st.columns(4)
        a.metric("Public market price", f"{last_price:,.8g}")
        b.metric("Latest completed candle", pd.Timestamp(last_candle["timestamp"]).strftime("%H:%M:%S UTC"))
        c.metric("Latest signal", str(signal.get("decision", "SKIP")), str(signal.get("regime", "")))
        d.metric("Feed", "CONNECTED", "Public endpoint responded")
        e, f, g, h = st.columns(4)
        e.metric("Paper equity estimate", f"₹{summary['equity_estimate']:,.2f}")
        f.metric("Realized net P&L", f"₹{summary['realized_net_pnl']:,.2f}")
        g.metric("Open-position unrealized P&L*", f"₹{mtm:,.2f}")
        h.metric("Daily loss guard", "HIT" if summary["daily_stop_hit"] else f"₹{summary['daily_loss_limit']:,.0f} limit")

        st.subheader("Current paper position")
        if open_trade:
            st.dataframe(pd.DataFrame([{
                "Symbol": open_trade["symbol"], "Side": open_trade["side"],
                "Quantity": open_trade["quantity"], "Entry": open_trade["entry"],
                "Stop": open_trade["stop"], "Target": open_trade["target"],
                "Opened (UTC)": open_trade["opened_at"], "Unrealized P&L* (₹)": mtm
            }]), use_container_width=True, hide_index=True)
        else:
            st.info("No open paper position.")

        st.subheader("Latest signal details")
        st.json(signal)
        st.subheader("Paper trade ledger")
        if history:
            st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
        else:
            st.caption("No paper trades recorded yet.")

        st.caption("*Unrealized P&L is a rough mark-to-market estimate before exit fees and slippage. INR accounting is only valid for INR-margined contracts. USDT instruments are intentionally prevented from opening paper positions in this version.")
        st.caption(f"Database: {DB_PATH.as_posix()} · Refresh interval: ~15 seconds · No live orders")
    except (CoinDCXPublicAPIError, ValueError, TypeError, KeyError) as exc:
        st.error(f"Live paper status could not refresh: {exc}")
        st.caption("Check the public-data connection and selected active instrument. No live order was attempted.")

live_panel()
