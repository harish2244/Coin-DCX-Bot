# Automatic Runner Liveness & Freshness Specification

## Goal
The dashboard must independently determine whether the hosted paper-trading worker is actually reporting recently. A successful Pages deployment or a loaded dashboard must never be presented as proof that the trading worker is live.

## Required behavior
1. Fetch `data/expert-pick-paper-trades.json` with a cache-busting query and `cache: no-store`.
2. Recheck automatically every 30 seconds while the dashboard is open.
3. Parse `updated_at` (fallback: `health.last_run_at`) as the timestamp of the latest persisted worker report.
4. Show **LIVE CHECKS · FRESH** only when the timestamp is valid, the report age is at most 15 minutes, and `health.runner_completed === true`.
5. Otherwise show **STALE / NOT LIVE** in red. Include the report timestamp and age, and explicitly warn that displayed prices/signals must be treated as stale.
6. If fetch fails, JSON is invalid, or timestamp is missing/invalid, show a visible error/stale state. Never silently keep a green state.
7. Keep the PAPER ONLY / NO LIVE ORDERS label visible. A fresh report means only that the scheduled paper worker recently completed; it does not mean live exchange trading is enabled.
8. The freshness indicator must use the paper runner's report, not the GitHub Pages deployment status or the separate dashboard/backtest data.
9. The threshold is 15 minutes for a 10-minute scheduled workflow, allowing normal schedule delays while flagging a missed run.
10. Browser polling only runs while the page is open. GitHub Actions remains responsible for scheduled checks; this UI cannot prove that a future run will occur.

## Acceptance tests
- Report updated 0–15 minutes ago + runner_completed true => green fresh status.
- Report older than 15 minutes => red stale status, with age.
- runner_completed false/missing => red stale status.
- Invalid/missing timestamp, HTTP error, offline/network failure, or invalid JSON => visible error/stale state.
- After opening the page, timestamp freshness is reevaluated within 30 seconds without a manual reload.
- No condition in this spec can enable or place live orders.
