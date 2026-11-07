# FX Scalper: Risk Limits

Status: POLICY values pending CFO (Elena Petrova) approval; the T-313 source records this file as DRAFT for CFO review. Proposed for implementation on branch t-308-fx-scalper; nothing is merged to main. Values marked POLICY are company risk settings. Values marked ASSUMPTION are planning inputs, not measured data, and must be replaced before go-live.
Owner: Finance (FP&A). Implementation: Engineering, via GitHub PR to the fx-scalper feature branch.
Scope: every order the fx-scalper strategy places, on any account it trades.

## 1. Named parameters

| Parameter | Value | Type | Unit / meaning |
|---|---|---|---|
| RISK_PER_TRADE_PCT | 0.50 | POLICY | Percent of account equity risked on a single trade, measured at the stop-loss price. |
| MAX_LEVERAGE_POLICY | 10 | POLICY (CFO) | Policy cap on leverage the strategy may use, as a ratio (10 = 10:1). Defined here only. Effective value (MAX_LEVERAGE_EFFECTIVE) is defined in section 3. |
| BROKER_MAX_LEVERAGE | read at runtime | BROKER INPUT | The leverage limit published by the broker for the instrument and account. Not quoted in this document. |
| DAILY_LOSS_LIMIT_PCT | 2.00 | POLICY | Percent of session-start equity. Reaching it halts trading for the rest of the session (section 5). |
| MAX_TRADES_PER_DAY | 20 | POLICY | Maximum number of new entry orders opened in one session. |
| MAX_OPEN_POSITIONS | 3 | POLICY | Maximum number of positions open at the same time, across all instruments. |
| spread_pips | 1.0 | ASSUMPTION | Planning value for the average spread at entry, in pips. Replace with measured spread before go-live. |
| commission_pips | 0.5 | ASSUMPTION | Planning value for round-trip commission expressed in pips. Replace with the commission schedule once confirmed. |
| slippage_pips | 0.5 | ASSUMPTION | Planning value for expected execution slippage, in pips. Replace with measured fill data. |
| STOP_LOSS_PIPS | per order (no fixed value) | ORDER INPUT (section 2) | Stop-loss distance in pips for each entry. Must be strictly greater than break_even_pips (section 4). Source: risk.md §2, §7 step 5. |
| LOT_STEP, MIN_UNITS | read at runtime | BROKER INPUT | Broker lot-size step and minimum units. Not quoted in this document. Source: risk.md §2. NOT IN T-313 (flag F5). |

No broker figure is quoted in this document. Every broker-dependent value is read at runtime from the broker.

## 2. Position sizing

Formula:

units = (equity × RISK_PER_TRADE_PCT / 100) / (stop_loss_pips × pip_value_per_unit_in_account_ccy)

Variables:
- equity: account equity in account currency, taken at the time of the order (balance plus open floating P&L). If equity cannot be read, no order is sent.
- RISK_PER_TRADE_PCT / 100: the risk fraction (0.50% = 0.005).
- stop_loss_pips: distance from the planned entry price to the stop-loss price, in pips. Must be greater than zero, and stop_loss_pips must be strictly greater than break_even_pips (section 4), matching section 7 step 5. Fail closed: if stop_loss_pips is equal to or below break_even_pips, no order is sent.
- pip_value_per_unit_in_account_ccy: value in account currency of one pip for one unit of the instrument, computed from the instrument's pip size and the current conversion rate to account currency. Read from the broker specification at runtime; not estimated.

Rules:
- Compute units and round DOWN to the broker's minimum lot-size step. If the rounded result is below the broker minimum, do not trade. Never round up, because rounding up would raise the risk above RISK_PER_TRADE_PCT.
- Worked form only, no broker values: the risk amount in account currency equals equity × RISK_PER_TRADE_PCT / 100. Units are sized so that a stop-loss hit loses that amount.

## 3. Leverage

- MAX_LEVERAGE_EFFECTIVE = min(MAX_LEVERAGE_POLICY, BROKER_MAX_LEVERAGE). Source tag: MAX_LEVERAGE_POLICY = POLICY (CFO), section 1; BROKER_MAX_LEVERAGE = BROKER INPUT, read at runtime, not quoted in this document. Policy cap = MAX_LEVERAGE_POLICY (10); broker published limit = BROKER_MAX_LEVERAGE read at runtime.
- The broker limit prevails. If the broker limit is lower than the policy cap, the lower value applies. The policy cap never allows leverage above the broker limit.
- If BROKER_MAX_LEVERAGE cannot be read, the system must not place orders (fail closed).
- Position notional must be checked before each order: notional ÷ equity must not exceed MAX_LEVERAGE_EFFECTIVE.

## 4. Break-even distance

break_even_pips = spread_pips + commission_pips + slippage_pips

- spread_pips, commission_pips and slippage_pips are all ASSUMPTION inputs (section 1) until replaced with measured or contracted values.
- With the planning values above, break_even_pips = 1.0 + 0.5 + 0.5 = 2.0 pips. This is a planning figure only.
- A trade is valid only if its target distance exceeds break_even_pips. Orders whose target is at or below this distance are rejected.

## 5. Daily loss limit and session reset

- Session start equity (SSE) is recorded at the start of each session.
- Daily loss = SSE − current equity (realised plus floating). If daily loss ≥ DAILY_LOSS_LIMIT_PCT × SSE / 100, the strategy halts.
- Halt: no new orders are sent, and open positions are closed at the next available price per the exit rules. Halt lasts until the next session.
- Session reset: a new session begins at 00:00 UTC each day (session boundary is a POLICY setting; change only with CFO approval). At the boundary, the halt flag clears, MAX_TRADES_PER_DAY counter resets to zero, and SSE is re-recorded from current equity.
- A halt does not reset early for any reason within the session, including a recovery in floating P&L.
- The halt flag and SSE must persist across process restarts. If the system restarts mid-session, it reads the stored halt state and does not resume trading.

## 6. Trade and position counters

- MAX_TRADES_PER_DAY: counts new entry orders accepted in the current session. Reaching the limit blocks new entries until the session reset. Stop-loss and take-profit exits do not count.
- MAX_OPEN_POSITIONS: counts open positions at the time of the entry check. A new entry is blocked when the count equals MAX_OPEN_POSITIONS.

## 7. Enforcement order (for the risk manager code)

Each entry request is checked in this order, and the first failure rejects the order:
1. Daily-loss halt flag is not set.
2. Trades today < MAX_TRADES_PER_DAY.
3. Open positions < MAX_OPEN_POSITIONS.
4. BROKER_MAX_LEVERAGE readable; MAX_LEVERAGE_EFFECTIVE computed per section 3.
5. stop_loss_pips strictly greater than break_even_pips, and target distance strictly greater than break_even_pips.
6. Units computed per section 2 and rounded down to the lot step; units ≥ broker minimum.
7. Post-trade leverage ≤ MAX_LEVERAGE_EFFECTIVE.

## 8. Enforceable settings (config keys)

The risk manager reads these keys from a config file at start-up and on every entry check. Code must not hard-code them. A missing, non-numeric or out-of-range key means no orders are sent (fail closed).

| Config key | Value | Type | Valid range | Source tag | Enforced at |
|---|---|---|---|---|---|
| risk_per_trade_pct | 0.50 | float, % of equity | 0 < x ≤ 0.50 | POLICY (CFO) | section 7 step 6 |
| max_leverage_policy | 10 | float, ratio | 1 ≤ x ≤ 10 | POLICY (CFO) | section 7 steps 4 and 7 |
| broker_max_leverage | runtime | float, ratio | > 0 | BROKER INPUT (read from broker, not quoted here) | section 7 step 4 |
| effective_max_leverage | min(max_leverage_policy, broker_max_leverage) | derived | never above broker_max_leverage | derived | section 7 step 7 |
| daily_loss_limit_pct | 2.00 | float, % of session-start equity | 0 < x ≤ 2.00 | POLICY (CFO) | section 5, checked on every equity update and section 7 step 1 |
| max_trades_per_day | 20 | integer | 1 ≤ x ≤ 20 | POLICY (CFO) | section 7 step 2 |
| max_open_positions | 3 | integer | 1 ≤ x ≤ 3 | POLICY (CFO) | section 7 step 3 |
| session_reset_utc | 00:00 | time, UTC | fixed | POLICY (CFO) | section 5 |
| spread_pips | 1.0 | float, pips | ≥ 0 | ASSUMPTION | section 4 |
| commission_pips | 0.5 | float, pips | ≥ 0 | ASSUMPTION | section 4 |
| slippage_pips | 0.5 | float, pips | ≥ 0 | ASSUMPTION | section 4 |
| break_even_pips | spread_pips + commission_pips + slippage_pips | derived, pips | > 0 | derived | section 4, section 7 step 5 |
| stop_loss_pips | per order (input, section 2) | float, pips | strictly > break_even_pips | ORDER INPUT; rule from risk.md §2 and §7 step 5 | section 2, section 7 step 5 |
| target_pips | per order (input) | float, pips | strictly > break_even_pips | ORDER INPUT; rule from risk.md §4 and §7 step 5 | section 4, section 7 step 5 |
| lot_step, min_units | read at runtime | float / integer | > 0 | BROKER INPUT (read from broker spec, not quoted here). NOT IN T-313 (flag F5) | section 2, section 7 step 6 |
| pip_value_per_unit_in_account_ccy | read at runtime | float, account ccy per pip per unit | > 0 | BROKER INPUT (read from broker spec, not quoted here). NOT IN T-313 (flag F5) | section 2 |
| equity | read at order time | float, account ccy | > 0 | ACCOUNT INPUT (balance plus floating P&L). If unreadable, no order | section 2, section 5 |

Stop-loss in pips is an input to every entry request (stop_loss_pips, section 2), not a fixed setting. It must be greater than zero and strictly greater than break_even_pips, or the order is rejected.

Test cases: the per-limit test matrix is finance/T-313-risk-test-matrix.md (cases T1 to T22, covering each enforced limit in sections 2 to 6). Open item in that matrix: flag F1 / case T22 (stop exactly equal to break_even_pips). Sections 2 and 7 step 5 of this file both state strictly greater, so T22 is rejected; the F1 note in the matrix cites older §2 wording. CFO to confirm the strict rule. Values not available from T-313 or this file: broker leverage, lot step, minimum units and pip value (BROKER INPUT, read at runtime; flags F4 and F5).

## 9. Change control

Any change to a POLICY value needs CFO approval and a new version of this file. ASSUMPTION values are replaced with measured values once available, and the file is updated in the same PR.