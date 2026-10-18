from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from bot.coindcx_public import (
    CoinDCXPublicAPIError,
    get_active_futures_instruments,
    get_futures_candles,
    get_futures_current_prices,
)
from bot.indicators import add_indicators, normalize_candles
from bot.risk import position_plan
from bot.strategy import assess_candidate
from bot.paper_broker import PaperBroker

st.set_page_config(page_title="Coin DCX Hybrid Paper Trader", page_icon="📊", layout="wide")
st.title("Coin DCX Hybrid Paper Trader")
st.warning("PAPER MODE ONLY — public market-data reads only; no API keys, authenticated actions, or live orders.")

with st.sidebar:
    st.header("Account and risk")
    equity = st.number_input("Paper equity (₹)", min_value=1000.0, value=10000.0, step=1000.0)
    leverage = st.number_input("Leverage reference (×)", min_value=1.0, max_value=20.0, value=3.0, step=1.0)
    risk_pct = st.number_input("Risk per trade (%)", min_value=0.1, max_value=1.0, value=0.5, step=0.1)
    daily_limit = st.number_input("Daily loss stop (₹)", min_value=50.0, value=200.0, step=50.0)

config = (equity, daily_limit)
if "broker" not in st.session_state or st.session_state.get("broker_config") != config:
    st.session_state.broker = PaperBroker(equity, daily_limit)
    st.session_state.broker_config = config
broker: PaperBroker = st.session_state.broker

@st.cache_data(ttl=60, show_spinner=False)
def cached_instruments(margin_currency: str) -> list[str]:
    return get_active_futures_instruments(margin_currency)

@st.cache_data(ttl=15, show_spinner=False)
def cached_prices() -> dict:
    return get_futures_current_prices()

@st.cache_data(ttl=15, show_spinner=False)
def cached_candles(pair: str, resolution: str, lookback_minutes: int) -> pd.DataFrame:
    return get_futures_candles(pair, resolution, lookback_minutes)

source = st.radio("Market data source", ["Upload CSV (offline)", "CoinDCX Futures (public data)"], horizontal=True)
candles_raw = None
symbol = ""
expert_pick = False
allow_paper_entry = False

if source == "Upload CSV (offline)":
    uploaded = st.file_uploader("Upload one-market OHLCV CSV", type=["csv"])
    st.caption("Required columns: timestamp, open, high, low, close, volume. At least 55 valid candles.")
    if uploaded:
        try:
            candles_raw = normalize_candles(pd.read_csv(uploaded))
            symbol = st.text_input("Market symbol", "BTC/USDT").strip() or "UNNAMED"
            expert_pick = st.checkbox("Symbol is in Expert Picks", True)
            allow_paper_entry = True
        except Exception as exc:
            st.error(f"Could not process CSV: {exc}")
else:
    st.caption("Read-only public endpoints. Prices/candles are market data, not an order connection.")
    left, right = st.columns([1, 2])
    margin_currency = left.selectbox("Instrument list margin mode", ["USDT", "INR"], index=0)
    resolution_label = right.selectbox("Candle interval", ["1 minute", "5 minutes", "1 hour"], index=1)
    resolution = {"1 minute": "1", "5 minutes": "5", "1 hour": "60"}[resolution_label]
    lookback = st.slider("Candle lookback (minutes)", min_value=60, max_value=1440, value=360, step=60)
    if st.button("Refresh public market data"):
        st.cache_data.clear()
        st.rerun()
    try:
        instruments = cached_instruments(margin_currency)
        if not instruments:
            st.warning("CoinDCX returned no active instruments for this margin mode.")
        else:
            default_pair = "B-BTC_USDT" if "B-BTC_USDT" in instruments else instruments[0]
            pair = st.selectbox("Futures instrument", instruments, index=instruments.index(default_pair))
            symbol = pair
            expert_pick = st.checkbox("Symbol is in Expert Picks", False, key="live_expert_pick")
            if st.button("Load latest candles and assess"):
                st.session_state["live_pair"] = pair
                st.session_state["live_resolution"] = resolution
                st.session_state["live_lookback"] = lookback
                st.session_state["live_expert_pick_value"] = expert_pick
            if st.session_state.get("live_pair") == pair:
                try:
                    live_raw = cached_candles(
                        pair,
                        st.session_state.get("live_resolution", resolution),
                        st.session_state.get("live_lookback", lookback),
                    )
                    candles_raw = normalize_candles(live_raw)
                    expert_pick = bool(st.session_state.get("live_expert_pick_value", expert_pick))
                    prices = cached_prices().get("prices", {})
                    price_row = prices.get(pair, {})
                    if price_row:
                        st.metric("Latest public futures price", f"{float(price_row.get('ls', 0)):,.8g}",
                                  f"24h change {float(price_row.get('pc', 0)):.2f}%")
                    st.caption(f"Instrument: {pair} · candle interval used: {st.session_state.get('live_resolution', resolution)} · data source: CoinDCX public API")
                except (CoinDCXPublicAPIError, ValueError, TypeError) as exc:
                    st.error(f"Could not load live market data: {exc}")
            else:
                st.info("Choose an instrument and press **Load latest candles and assess** to fetch data.")
    except (CoinDCXPublicAPIError, ValueError, TypeError) as exc:
        st.error(f"Could not retrieve CoinDCX instrument list: {exc}")

if candles_raw is not None:
    try:
        candles = add_indicators(candles_raw)
        latest = candles.iloc[-1]
        a, b, c = st.columns(3)
        a.metric("Candles loaded", f"{len(candles):,}")
        b.metric("Latest candle close", f"{latest.close:,.8g}")
        c.metric("RSI 14", f"{latest.rsi14:.1f}" if pd.notna(latest.rsi14) else "N/A")
        st.line_chart(candles.set_index("timestamp")[["close", "ema20", "ema50", "vwap30"]].tail(300))
        with st.expander("Latest candle data"):
            st.dataframe(candles.tail(20), use_container_width=True)

        st.subheader("Hybrid candidate assessment")
        signal = assess_candidate(candles, symbol, expert_pick)
        st.json(signal)
        side = signal.get("decision")
        if source != "Upload CSV (offline)":
            st.info("Live-source assessment is read-only. No simulated entry is enabled from this feed yet; quote-currency conversion and cost-aware fills must be implemented first.")
        elif side in {"LONG", "SHORT"}:
            st.info("This is a heuristic paper signal, not a recommendation or profitability guarantee.")
            entry = st.number_input("Hypothetical entry", min_value=0.000001, value=float(latest.close), format="%.8f")
            stop_pct = st.number_input("Stop distance (%)", min_value=0.1, max_value=10.0, value=0.5, step=0.1)
            rr = st.number_input("Reward/risk ratio", min_value=1.0, max_value=5.0, value=1.5, step=0.5)
            stop = entry * (1 - stop_pct/100) if side == "LONG" else entry * (1 + stop_pct/100)
            target = entry + abs(entry-stop)*rr if side == "LONG" else entry - abs(entry-stop)*rr
            plan = position_plan(broker.equity, entry, stop, leverage, risk_pct)
            st.write({"stop": stop, "target": target, **plan})
            if broker.daily_stop_hit:
                st.error("Daily loss stop reached; entries disabled.")
            elif broker.has_open_position:
                st.info("One simulated position is already open.")
            elif st.button("Open simulated paper position"):
                try:
                    broker.open_position(symbol, side, plan["quantity_base_asset"], entry, stop, target,
                                         datetime.now(timezone.utc).isoformat())
                    st.success("Simulated position opened in this app session.")
                except ValueError as exc:
                    st.error(str(exc))

        if source == "Upload CSV (offline)":
            st.subheader("Manual stop/target check")
            opened = [p for p in broker.positions if p.status == "OPEN"]
            if opened:
                p = opened[0]
                st.write(f"{p.side} {p.symbol}: entry {p.entry:.8g}, stop {p.stop:.8g}, target {p.target:.8g}")
                price = st.number_input("Price to check", min_value=0.000001, value=float(latest.close), format="%.8f")
                if st.button("Check stop / target"):
                    closed = broker.update_price(p.symbol, price)
                    if closed:
                        st.success(f"Closed at simulated {closed.exit_reason}; P&L ₹{closed.pnl:,.2f}")
                    else:
                        st.info("Neither level was touched at this supplied price.")
            else:
                st.caption("No open simulated positions.")
    except Exception as exc:
        st.error(f"Could not process candles: {exc}")
elif source == "Upload CSV (offline)":
    st.info("Upload candle data to calculate indicators and assess a manually marked Expert Pick.")

st.subheader("Paper account")
x, y, z = st.columns(3)
x.metric("Paper equity", f"₹{broker.equity:,.2f}")
y.metric("Realized simulated P&L", f"₹{broker.realized_pnl:,.2f}")
z.metric("Daily loss guard", "HIT" if broker.daily_stop_hit else f"₹{broker.daily_loss_limit:,.0f}")
if broker.positions:
    history = pd.DataFrame(broker.history())
    st.dataframe(history, use_container_width=True)
    st.download_button("Download trade history CSV", history.to_csv(index=False).encode(), "paper_trades.csv", "text/csv")

st.caption("Public market data is now available for inspection. Live order placement, API authentication, fees, funding, spread/slippage-aware fills, liquidation simulation, persistent storage, and automatic stop monitoring remain unimplemented. Paper positions are only created from CSV mode and stop/target checks remain manual.")
