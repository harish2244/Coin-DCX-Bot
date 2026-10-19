from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests

BASE_URL = "https://public.coindcx.com"
API_URL = "https://api.coindcx.com"
TIMEOUT_SECONDS = 15


class CoinDCXPublicAPIError(RuntimeError):
    """Raised when a public CoinDCX market-data request fails."""


def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise CoinDCXPublicAPIError(f"CoinDCX public market-data request failed: {exc}") from exc


def get_active_futures_instruments(margin_currency: str = "USDT") -> list[str]:
    """Return active futures pair IDs. No API key or trading permission is used."""
    margin = margin_currency.upper()
    if margin not in {"USDT", "INR"}:
        raise ValueError("margin_currency must be USDT or INR")
    payload = _get_json(
        f"{API_URL}/exchange/v1/derivatives/futures/data/active_instruments",
        params={"margin_currency_short_name[]": margin},
    )
    if not isinstance(payload, list):
        raise CoinDCXPublicAPIError(f"Unexpected active-instruments response: {str(payload)[:250]}")
    return sorted({str(pair) for pair in payload if isinstance(pair, str) and pair.startswith("B-")})


def get_futures_current_prices() -> dict[str, Any]:
    """Return the public futures current-price snapshot."""
    payload = _get_json(f"{BASE_URL}/market_data/v3/current_prices/futures/rt")
    if not isinstance(payload, dict):
        raise CoinDCXPublicAPIError("Unexpected futures current-prices response format")
    if isinstance(payload.get("prices"), dict):
        return payload
    # Some public responses expose the symbol-to-price map directly.
    if payload and all(isinstance(v, dict) for v in payload.values()):
        return {"prices": payload}
    raise CoinDCXPublicAPIError(f"Unexpected futures current-prices fields: {list(payload)[:12]}")


def get_futures_candles(pair: str, resolution: str = "1", lookback_minutes: int = 240) -> pd.DataFrame:
    """Fetch public futures candles using CoinDCX's documented futures endpoint."""
    if not isinstance(pair, str) or not pair.startswith("B-") or len(pair) > 64:
        raise ValueError("Select a valid CoinDCX futures pair")
    if resolution not in {"1", "5", "60", "1D"}:
        raise ValueError("resolution must be one of: 1, 5, 60, 1D")
    if not 60 <= int(lookback_minutes) <= 10000:
        raise ValueError("lookback_minutes must be between 60 and 10000")

    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=int(lookback_minutes))
    payload = _get_json(
        f"{BASE_URL}/market_data/candlesticks",
        params={
            "pair": pair,
            "from": int(start.timestamp()),
            "to": int(now.timestamp()),
            "resolution": resolution,
            "pcode": "f",
        },
    )
    if isinstance(payload, dict):
        rows = payload.get("data")
        status = payload.get("s")
        if not isinstance(rows, list):
            raise CoinDCXPublicAPIError(
                f"CoinDCX candle response for {pair} has no data list (status={status!r}, keys={list(payload)[:12]})"
            )
        if status not in (None, "ok") and not rows:
            raise CoinDCXPublicAPIError(f"CoinDCX candle API status for {pair}: {status!r}")
    elif isinstance(payload, list):
        rows = payload
    else:
        raise CoinDCXPublicAPIError(f"Unexpected candle response type for {pair}: {type(payload).__name__}")
    if not rows:
        raise CoinDCXPublicAPIError(f"No candles returned for {pair}")
    frame = pd.DataFrame(rows)
    required = {"time", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise CoinDCXPublicAPIError(
            f"Futures-candles response for {pair} is missing fields; received {list(frame.columns)[:16]}"
        )
    frame = frame.rename(columns={"time": "timestamp"})
    frame["timestamp"] = pd.to_datetime(pd.to_numeric(frame["timestamp"], errors="coerce"), unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    if frame.empty:
        raise CoinDCXPublicAPIError(f"CoinDCX returned no valid candle rows for {pair}")
    return frame[["timestamp", "open", "high", "low", "close", "volume"]]
