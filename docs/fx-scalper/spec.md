# FX Scalper Spec — EUR/USD, 1-minute, practice only

Status: draft for review. Linked: T-308 (feature branch t-308-fx-scalper), T-310 (this task), T-319 (strategy sections and config keys), T-306 (parent objective).
Scope: paper trading only. The only broker endpoint is the OANDA v20 **practice** host. No live trading is in scope.
Position limits and sizing: governed by docs/fx-scalper/risk.md (see section 10). This spec does not restate them.

Config convention: every tunable value below is named by its key in `fx_scalper/config.py`, with its default in brackets. The config file is not yet in the repository; the keys are the contract for it.

## 1. Market and data

- Instrument: EUR/USD (OANDA instrument `EUR_USD`). Config: `INSTRUMENT = "EUR_USD"`.
- Pip size: 0.0001 (price quoted to 5 decimals). Config: `PIP_SIZE = 0.0001`.
- Timeframe: 1-minute candles (`M1`), mid prices for signals. Config: `CANDLE_GRANULARITY = "M1"`. Only **completed** candles are used. The bar being formed is never read.
- Trading hours: FX market only. No bars exist from Friday close to Sunday open, so no positions are opened on weekends (see section 8).

## 2. Indicators

- EMA(9) and EMA(21) on bar close. Seed = SMA of the first N closes, then the standard recursive EMA with k = 2/(N+1). Config: `EMA_FAST_PERIOD = 9`, `EMA_SLOW_PERIOD = 21`.
- RSI(14) on bar close, Wilder smoothing. Config: `RSI_PERIOD = 14`.

## 3. Entry rules

Evaluated on the close of bar i. The order is filled at the **open of bar i+1**.

- **Long:** EMA9 crosses above EMA21 (EMA9[i-1] <= EMA21[i-1] and EMA9[i] > EMA21[i]) AND RSI_LONG_MIN < RSI14[i] < RSI_LONG_MAX. Config: `RSI_LONG_MIN = 50`, `RSI_LONG_MAX = 70`.
- **Short:** EMA9 crosses below EMA21 (EMA9[i-1] >= EMA21[i-1] and EMA9[i] < EMA21[i]) AND RSI_SHORT_MIN < RSI14[i] < RSI_SHORT_MAX. Config: `RSI_SHORT_MIN = 30`, `RSI_SHORT_MAX = 50`.
- Maximum one open position at a time. Config: `MAX_OPEN_POSITIONS_STRATEGY = 1` (strategy-level; the portfolio limit is in risk.md).
- An entry is skipped if any entry gate in sections 5 to 8 blocks it at fill time.

## 4. Exit rules, pips and holding time

Checked in this order on every bar after entry:

1. **Stop-loss:** 4 pips from entry (design parameter). Config: `STOP_LOSS_PIPS = 4.0`. Triggered when the bar low (long) or high (short) reaches the level.
2. **Take-profit:** 6 pips from entry (design parameter). Config: `TAKE_PROFIT_PIPS = 6.0`. Triggered when the bar high (long) or low (short) reaches the level.
   - If both stop and target are inside the same bar, the **stop is assumed first** (conservative).
3. **Max holding time:** 15 minutes (design parameter). Config: `MAX_HOLD_MINUTES = 15`. Position closed at the open of the first bar at or after the limit.
4. **Opposite crossover:** EMA9 crosses EMA21 in the opposite direction on bar close. Position closed at that bar's close.
5. **Forced flat:** any open position is closed at the open of the first bar at or after Friday 20:45 UTC (section 8).

Stop and target are fixed in pips at fill. The spec does not use trailing stops. Pip distances are converted to price as pips × `PIP_SIZE`.

## 5. Session filter (UTC)

- Default session for **new entries**: 13:00 to 16:00 UTC, the London–New York overlap, as a fixed UTC window. Config: `SESSION_START_UTC = "13:00"`, `SESSION_END_UTC = "16:00"`.
- Session is a config value. Default values above.
- Exits are not restricted by session.
- Note: the London–New York overlap moves in UTC when the UK and US change clocks on different dates, so the fixed 13:00–16:00 UTC window is a simplification. Its effect on results is untested (see section 12).

## 6. News pause

- Driven by a config blackout file: `config/blackout.json`, a JSON list of `{"time": "<ISO-8601 UTC>", "label": "<text>"}`. Config: `BLACKOUT_FILE = "config/blackout.json"`.
- No new entries from 15 minutes before to 15 minutes after each listed time (±15 minutes). Config: `NEWS_PAUSE_MINUTES = 15` (applied both before and after).
- The file is maintained by hand. The company holds no calendar feed; the file is empty until someone populates it. An empty file means no news pauses are applied, so the file must be kept current before any use.
- Open positions keep their stop and target during a pause.

## 7. Spread rule

- Entry is blocked when the quoted spread at fill is greater than **1.5 pips**. Config: `MAX_SPREAD_PIPS = 1.5` (design parameter).
- Spread at fill = ask − bid of the fill bar (live) or the candle spread field (backtest).
- If the spread field is missing in a backtest file, the **assumed spread of 1.0 pip** is used (see section 9). Config: `ASSUMED_SPREAD_PIPS = 1.0` (ASSUMPTION).

## 8. Weekend and time limits

- No new entries after Friday 20:00 UTC. Config: `NO_ENTRY_FRIDAY_UTC = "20:00"`.
- All open positions are closed at the first bar at or after Friday 20:45 UTC. Config: `FORCED_FLAT_FRIDAY_UTC = "20:45"`.
- No positions are held over the weekend.
- Daily limits: see section 10. The design placeholders for trade counts and daily loss in the earlier draft are superseded by risk.md where they differ.

## 9. Costs: assumptions (not verified)

These are assumptions. Each must be checked against real practice data and the broker's published terms before any conclusion is drawn.

- **Spread — ASSUMPTION.** Backtest fallback is 1.0 pip (`ASSUMED_SPREAD_PIPS = 1.0`), applied as half-spread on each side of each fill. Live fills use the actual bid/ask.
- **Slippage — ASSUMPTION.** 0.5 pip adverse on every market fill (entries, time exits, crossover exits, forced flat) and on stop-loss fills. Take-profit fills are assumed at the target price with no slippage. Config: `SLIPPAGE_PIPS = 0.5`.
- **Commission — ASSUMPTION.** 0 pips in the model. The model assumes spread-only costs on this practice account. Config: `COMMISSION_PIPS = 0.0`. Verify against the OANDA fee schedule before relying on it. Note: risk.md section 1 uses a planning value of 0.5 pip for commission in its break-even calculation; the two must be reconciled before go-live.

## 10. Position limits and sizing (refer to risk.md)

- Position sizing, leverage, daily loss limit, maximum trades per day, maximum open positions and the break-even distance are governed by **docs/fx-scalper/risk.md**. This spec does not set those numbers.
- Where a value in this spec differs from risk.md, risk.md governs until the difference is resolved by Finance and Engineering.
- Current build placeholder: fixed order size `ORDER_UNITS = 1000` (design parameter, no risk-based sizing in this version). Config: `ORDER_UNITS = 1000`. risk.md section 2 defines risk-based sizing; this placeholder is to be replaced when it is implemented.

## 11. Logging

- Every closed trade is written to a CSV trade log with: trade_id, side, units, entry_time, entry_price, exit_time, exit_price, exit_reason, pnl_pips, spread_pips_assumed, slippage_pips_assumed, commission_pips_assumed, hold_minutes.
- `exit_reason` ∈ {stop_loss, take_profit, max_hold, opposite_cross, forced_flat}.
- Config: `TRADE_LOG_PATH = "logs/trades.csv"`.

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
- [x] Position limits referred to risk.md, no new numbers (section 10).
- [x] Every tunable value has a config key and default (sections 1–11, 14).

## 14. Config key register (`fx_scalper/config.py`)

| Key | Default | Type | Section |
|---|---|---|---|
| INSTRUMENT | "EUR_USD" | design | 1 |
| PIP_SIZE | 0.0001 | design | 1 |
| CANDLE_GRANULARITY | "M1" | design | 1 |
| EMA_FAST_PERIOD | 9 | design | 2 |
| EMA_SLOW_PERIOD | 21 | design | 2 |
| RSI_PERIOD | 14 | design | 2 |
| RSI_LONG_MIN | 50 | design | 3 |
| RSI_LONG_MAX | 70 | design | 3 |
| RSI_SHORT_MIN | 30 | design | 3 |
| RSI_SHORT_MAX | 50 | design | 3 |
| MAX_OPEN_POSITIONS_STRATEGY | 1 | design | 3 |
| STOP_LOSS_PIPS | 4.0 | design | 4 |
| TAKE_PROFIT_PIPS | 6.0 | design | 4 |
| MAX_HOLD_MINUTES | 15 | design | 4 |
| SESSION_START_UTC | "13:00" | design | 5 |
| SESSION_END_UTC | "16:00" | design | 5 |
| BLACKOUT_FILE | "config/blackout.json" | file path | 6 |
| NEWS_PAUSE_MINUTES | 15 | design | 6 |
| MAX_SPREAD_PIPS | 1.5 | design | 7 |
| ASSUMED_SPREAD_PIPS | 1.0 | ASSUMPTION | 7, 9 |
| NO_ENTRY_FRIDAY_UTC | "20:00" | design | 8 |
| FORCED_FLAT_FRIDAY_UTC | "20:45" | design | 8 |
| SLIPPAGE_PIPS | 0.5 | ASSUMPTION | 9 |
| COMMISSION_PIPS | 0.0 | ASSUMPTION | 9 |
| ORDER_UNITS | 1000 | design placeholder (risk.md governs) | 10 |
| TRADE_LOG_PATH | "logs/trades.csv" | file path | 11 |

Limits from risk.md (POLICY and BROKER values, including MAX_TRADES_PER_DAY, MAX_OPEN_POSITIONS, DAILY_LOSS_LIMIT_PCT, RISK_PER_TRADE_PCT, MAX_LEVERAGE) are not repeated here. They are read from risk.md and the broker.
