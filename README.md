# Coin DCX Bot

CoinDCX futures market-data and paper-trading prototype. **Live trading is disabled.**

## Current features
- Streamlit dashboard with CSV upload and read-only CoinDCX futures public market-data mode.
- Candle normalization and EMA20/EMA50, RSI14, rolling 30-candle VWAP approximation, and relative-volume indicators.
- Heuristic Expert-Pick-gated LONG/SHORT/SKIP assessment.
- In-memory CSV paper broker for manual simulation.
- SQLite-backed `PersistentPaperEngine` module for persisted paper positions/trade history, one-open-position limit, 0.5% planned risk default, 3x notional cap, daily loss guard, and estimated fee/slippage costs.
- GitHub Actions tests.

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
streamlit run app.py
```

## Risk configuration
Prototype defaults: starting equity ₹10,000, risk per trade 0.5% (₹50 before costs), leverage reference 3x, daily loss stop ₹200, one open position. These are simulation parameters, not a promise of maximum realized loss; gaps, slippage, fees and product-specific contract rules can cause losses beyond the planned risk.

## Important limitations
- The dashboard's live-data mode currently displays public market data and evaluates signals; it does **not** automatically execute positions.
- SQLite paper engine is implemented as a module but not yet wired into the live-data dashboard cycle.
- Public API response compatibility still requires runtime verification against the exchange.
- No verified historical backtest, funding/contract specification handling, currency conversion from USDT P&L to INR, or robust unattended scheduler yet.
- No authenticated API calls or live order placement exist. Never add exchange trading credentials to this repository.
