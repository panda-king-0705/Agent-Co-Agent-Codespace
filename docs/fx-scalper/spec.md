# FX Scalper Spec — EUR/USD, 1-minute, practice only

Status: draft for review. Linked: T-308 (feature branch t-308-fx-scalper), T-310 (this task), T-306 (parent objective).
Scope: paper trading only. The only broker endpoint is the OANDA v20 **practice** host. No live trading is in scope.

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

- **Long:** EMA9 crosses above EMA21 (EMA9[i-1] <= EMA21[i-1] and EMA9[i] > EMA21[i]) AND 50 < RSI14[i] < 70.
- **Short:** EMA9 crosses below EMA21 (EMA9[i-1] >= EMA21[i-1] and EMA9[i] < EMA21[i]) AND 30 < RSI14[i] < 50.
- Maximum one open position at a time.
- An entry is skipped if any entry gate in section 7 or 8 blocks it at fill time.

## 4. Exit rules

Checked in this order on every bar after entry:

1. **Stop-loss:** 4 pips from entry (design parameter). Triggered when the bar low (long) or high (short) reaches the level.
2. **Take-profit:** 6 pips from entry (design parameter). Triggered when the bar high (long) or low (short) reaches the level.
   - If both stop and target are inside the same bar, the **stop is assumed first** (conservative).
3. **Max holding time:** 15 minutes (design parameter). Position closed at the open of the first bar at or after the limit.
4. **Opposite crossover:** EMA9 crosses EMA21 in the opposite direction on bar close. Position closed at that bar's close.
5. **Forced flat:** any open position is closed at the open of the first bar at or after Friday 20:45 UTC (section 8).

Stop and target are fixed in pips at fill. The spec does not use trailing stops.

## 5. Session filter (UTC)

- Default session for **new entries**: 13:00 to 16:00 UTC, the London–New York overlap, as a fixed UTC window.
- Session is a config value (`session_start_utc`, `session_end_utc`). Default values above.
- Exits are not restricted by session.
- Note: the overlap in local time shifts with daylight saving. The spec uses UTC on purpose.

## 6. News pause

- Driven by a config blackout file: `config/blackout.json`, a JSON list of `{"time": "<ISO-8601 UTC>", "label": "<text>"}`.
- No new entries from 15 minutes before to 15 minutes after each listed time (±15 minutes).
- The file is maintained by hand. The company holds no calendar feed; the file is empty until someone populates it. An empty file means no news pauses are applied, so the file must be kept current before any use.
- Open positions keep their stop and target during a pause.

## 7. Spread rule

- Entry is blocked when the quoted spread at fill is greater than **1.5 pips** (`max_spread_pips`, design parameter).
- Spread at fill = ask − bid of the fill bar (live) or the candle spread field (backtest).
- If the spread field is missing in a backtest file, the **assumed spread of 1.0 pip** is used (see section 9).

## 8. Weekend and time limits

- No new entries after Friday 20:00 UTC.
- All open positions are closed at the first bar at or after Friday 20:45 UTC.
- No positions are held over the weekend.
- Daily limits (design parameters): maximum 10 trades per UTC day; stop trading for the day after a loss of 20 pips in that day.

## 9. Costs: assumptions (not verified)

These are assumptions. Each must be checked against real practice data and the broker's published terms before any conclusion is drawn.

- **Spread — ASSUMPTION.** Backtest fallback is 1.0 pip, applied as half-spread on each side of each fill. Live fills use the actual bid/ask.
- **Slippage — ASSUMPTION.** 0.5 pip adverse on every market fill (entries, time exits, crossover exits, forced flat) and on stop-loss fills. Take-profit fills are assumed at the target price with no slippage.
- **Commission — ASSUMPTION.** 0 pips in the model. The model assumes spread-only costs on this practice account. Verify against the OANDA fee schedule before relying on it.

## 10. Position sizing

- Fixed 1,000 units per trade (design parameter). No compounding and no risk-based sizing in this version.

## 11. Logging

- Every closed trade is written to a CSV trade log with: trade_id, side, units, entry_time, entry_price, exit_time, exit_price, exit_reason, pnl_pips, spread_pips_assumed, slippage_pips_assumed, commission_pips_assumed, hold_minutes.
- `exit_reason` ∈ {stop_loss, take_profit, max_hold, opposite_cross, forced_flat}.

## 12. Limitations

- **No backtest result exists yet.** The backtest script is written, but no candle file is in the repository. Results are not available yet and no performance claim is made in this spec.
- Pip values, session, hold time and daily limits are untested design parameters.
- A 1-minute EMA crossover strategy with 4-pip stops and 6-pip targets may have a negative expectancy after costs. The backtest exists to measure this, not to assume it.
- The model is simple on purpose: one instrument, one position, no news feed, no order-book data.

## 13. Acceptance checklist (for review)

- [x] EUR/USD, M1 candles, EMA 9/21 crossover entry and exit, RSI(14) filter (sections 1–4).
- [x] Pip-based take-profit and stop-loss, max holding time (section 4).
- [x] UTC session, default London–NY overlap 13:00–16:00 UTC (section 5).
- [x] ±15-minute news pause from config blackout file (section 6).
- [x] Max-spread rule (section 7) and no weekend positions (section 8).
- [x] Spread, commission and slippage each labelled as an assumption (section 9).
