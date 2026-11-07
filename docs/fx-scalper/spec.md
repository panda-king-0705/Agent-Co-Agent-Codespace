# FX Scalper Spec — EUR/USD, 1-minute, practice only

Status: complete for T-323 review (spec committed before any code). No backtest result exists yet.
Linked: T-308 (feature branch t-308-fx-scalper), T-310 (original spec), T-319 (strategy sections and config keys), T-323 (completion and settings/broker interface), T-306 (parent objective).
Scope: paper trading only. The only broker endpoint is the OANDA v20 **practice** host. No live trading is in scope.
Position limits and sizing: governed by docs/fx-scalper/risk.md (see section 10). This spec does not restate them.

Config convention: every tunable value below is named by its key in `fx_scalper/config.py`, with its default in brackets. The keys are the contract for `fx_scalper/settings.py` (see section 15).

## 1. Market and data

- Instrument: EUR/USD (OANDA instrument `EUR_USD`).
- Pip size: 0.0001 (price quoted to 5 decimals).
- Timeframe: 1-minute candles (`M1`), mid prices for signals. Only **completed** candles are used. The bar being formed is never read.
- Trading hours: FX market only. No bars exist from Friday close to Sunday open, so no positions are opened on weekends (see section 8).

## 2. Indicators

- EMA(9) and EMA(21) on bar close. Seed = SMA of the first N closes, then the standard recursive EMA with k = 2/(N+1).
- RSI(14) on bar close, Wilder smoothing.

## 3. Entry rules

Evaluated on the close of bar i. The order is filled at the **open of bar i+1**.
- **Long:** EMA9 crosses above EMA21 and RSI14 is in the configured long range.
- **Short:** EMA9 crosses below EMA21 and RSI14 is in the configured short range.
- Maximum one open strategy position. An entry is skipped if any gate blocks it at fill time.

## 4. Exit rules, pips and holding time

Checked in order on every bar after entry: stop-loss, take-profit (stop first if both within same bar), max holding time, opposite crossover, forced flat Friday 20:45 UTC. Stop/target fixed in pips at fill; no trailing stops.

## 5. Session filter (UTC)
Default new-entry session is 13:00 to 16:00 UTC. Exits are unrestricted. The fixed UTC window is a daylight-saving simplification.

## 6. News pause
No new entries ±15 minutes around each timestamp in the maintained JSON blackout file. The file is empty until populated; keep it current before use. Open positions keep stops and targets.

## 7. Spread rule
Entry is blocked above 1.5 pips. Missing backtest spread uses the 1.0-pip assumption.

## 8. Weekend and time limits
No entries after Friday 20:00 UTC; all positions close at or after Friday 20:45 UTC; no weekend holds. Daily limits are governed by risk.md.

## 9. Costs: assumptions (not verified)
These are assumptions and must be checked against practice data and broker terms before conclusions.
- **Spread — ASSUMPTION.** Backtest fallback 1.0 pip; live uses actual bid/ask.
- **Slippage — ASSUMPTION.** 0.5 pip adverse on market and stop fills; take-profit at target without slippage.
- **Commission — ASSUMPTION.** 0.5 pip planning value from risk.md, charged once per round trip. It is not a quoted broker fee; verify against OANDA terms before relying on it and update risk.md and this spec together.

## 10. Position limits and sizing (refer to risk.md)
Position sizing, leverage, daily loss, trade count, open positions and break-even distance are governed by docs/fx-scalper/risk.md. Where values differ, risk.md governs until resolved. The earlier fixed-size placeholder is superseded by risk-based sizing there.

## 11. Logging
Every closed trade is written to CSV with trade identifiers, entry/exit details, costs, P&L and hold duration.

## 12. Limitations
No backtest result exists yet; no performance claim is made. Pip values, session, hold time and daily limits are untested. The simple one-instrument model may have negative expectancy after costs.

## 13. Acceptance checklist
- [x] EUR/USD, M1, EMA crossover and RSI filter.
- [x] Pip exits, max holding time, UTC session, news pause, spread rule and weekend close.
- [x] Costs labelled assumptions; risk settings referred to risk.md.
- [x] Credentials environment-only and live mode off by default.

## 14. Config key register
See fx_scalper/config.py and risk.md for the controlling defaults and policy limits.

## 15. Credentials and run mode
Credentials are read only from OANDA_API_TOKEN and OANDA_ACCOUNT_ID environment variables; missing variables fail without printing values. LIVE_MODE is off by default and live trading is out of scope. Practice endpoint only; secrets never enter logs.
