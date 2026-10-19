# Coin DCX Bot

CoinDCX futures market-data monitor and paper-trading prototype. **Live trading is disabled.**

## Signal and paper dashboard
Run locally:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
streamlit run app.py
```
Open **Live Paper Status** from Streamlit's page navigation. The dashboard reads public CoinDCX futures data and records, in SQLite:
- Each unique completed-candle signal event by symbol and timestamp.
- The baseline candidate signal and regime-model decision separately.
- Whether both models agree on LONG or SHORT, plus reasons and indicator context.
- Simulated paper positions, entry/stop/target, close reason, gross P&L, estimated fees/slippage and net P&L.
- Summary metrics: realized P&L, estimated equity, win rate, wins/losses, profit factor, expectancy, max drawdown, signal counts and open trades.
- CSV exports for the signal journal and paper-trade ledger.

The dashboard refreshes while its browser session is open; it is not an always-on hosted service. SQLite storage persists on the machine or host where the app runs. If hosted on ephemeral storage, the database may be lost on redeploy unless persistent storage is configured.

## Paper-only safety
- The dashboard uses public market-data endpoints only. It has no authenticated order-placement calls and cannot place live orders.
- Automatic entries are OFF by default. If enabled, only signals confirmed by agreement between the baseline signal and regime model can open simulated positions.
- INR-denominated accounting is supported for INR-margined contracts in this version. USDT pairs are monitor-only to avoid mixing currencies.
- Planned risk defaults to 0.5% of starting equity (₹50 on ₹10,000), leverage reference 3x, and daily realized loss stop ₹200. These are simulation controls, not guaranteed maximum losses.
- Simulated fills and P&L are approximate. Funding, liquidation, exchange-specific contract multipliers and all market microstructure effects are not fully modeled.
- Signal thresholds are experimental and not demonstrated to be profitable. Validate with walk-forward and out-of-sample testing before relying on the model.

## Existing features
- CSV/offline analysis and read-only public futures data in the original dashboard.
- EMA20/EMA50, RSI14, rolling VWAP approximation, relative volume and regime assessment.
- SQLite-backed paper ledger and GitHub Actions tests.
