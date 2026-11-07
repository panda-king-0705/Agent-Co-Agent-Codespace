"""Risk manager for the FX scalper (docs/fx-scalper/risk.md, sections 2-8).

Enforcement order follows risk.md section 7; the first failure rejects the order.
Fail closed: unreadable equity, broker inputs or timestamps reject the order.

Sizing (T-367, engineering decision by Kenji Tanaka, engineering/T-367-sizing-decision.md):
    risk_units = (equity x RISK_PER_TRADE_PCT / 100) / (stop_pips x pip_value)   # risk.md section 2
    cap_units  = MAX_LEVERAGE_EFFECTIVE x equity / price                          # risk.md section 3
    units      = floor_to_lot_step(min(risk_units, cap_units))
Reject only if units fall below the broker minimum. Risk per trade never exceeds 0.50%.

ASSUMPTION: commission_pips = 0.5 is the risk.md section 1 planning value.
OPEN CONFLICT: spec.md section 9 uses COMMISSION_PIPS = 0.0. Not resolved here;
must be reconciled by Finance and Engineering before go-live.
Spread and slippage (1.0 and 0.5 pips) are also ASSUMPTION planning values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone

# POLICY values (risk.md section 1 and section 8; CFO approval required to change)
RISK_PER_TRADE_PCT = 0.50
MAX_LEVERAGE_POLICY = 10.0
DAILY_LOSS_LIMIT_PCT = 2.00
MAX_TRADES_PER_DAY = 20
MAX_OPEN_POSITIONS = 3

# ASSUMPTION values (risk.md section 1)
SPREAD_PIPS = 1.0
COMMISSION_PIPS = 0.5  # ASSUMPTION; conflicts with spec.md section 9 (0.0), open
SLIPPAGE_PIPS = 0.5


class RiskInputError(ValueError):
    """Raised internally when an input is missing, non-numeric or out of range."""


@dataclass(frozen=True)
class Config:
    risk_per_trade_pct: float = RISK_PER_TRADE_PCT
    max_leverage_policy: float = MAX_LEVERAGE_POLICY
    daily_loss_limit_pct: float = DAILY_LOSS_LIMIT_PCT
    max_trades_per_day: int = MAX_TRADES_PER_DAY
    max_open_positions: int = MAX_OPEN_POSITIONS
    spread_pips: float = SPREAD_PIPS
    commission_pips: float = COMMISSION_PIPS
    slippage_pips: float = SLIPPAGE_PIPS

    @property
    def break_even_pips(self) -> float:
        return self.spread_pips + self.commission_pips + self.slippage_pips


@dataclass(frozen=True)
class BrokerInputs:
    """Values read from the broker at runtime. None means unreadable."""

    max_leverage: float | None  # BROKER_MAX_LEVERAGE
    lot_step: float | None  # LOT_STEP
    min_units: float | None  # MIN_UNITS
    pip_value_per_unit: float | None  # account ccy per pip per unit


@dataclass(frozen=True)
class EntryRequest:
    stop_pips: float
    target_pips: float
    entry_price: float  # price in account currency per unit


@dataclass(frozen=True)
class Decision:
    allowed: bool
    units: float = 0.0
    reason: str = ""


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RiskInputError(f"{name} is not a number")
    v = float(value)
    if not math.isfinite(v):
        raise RiskInputError(f"{name} is not finite")
    return v


def _positive(value: object, name: str) -> float:
    v = _number(value, name)
    if v <= 0:
        raise RiskInputError(f"{name} must be > 0")
    return v


def _utc_day(now: datetime) -> date:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise RiskInputError("timestamp must be timezone-aware")
    return now.astimezone(timezone.utc).date()


class RiskManager:
    """Holds per-session state (session start equity, trade count, halt flag)."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self.session_day: date | None = None
        self.session_start_equity: float | None = None
        self.trades_today = 0
        self.halted = False

    def observe_equity(self, now: datetime, equity: object) -> float | None:
        """Roll the session at 00:00 UTC and update the halt flag.

        Returns the valid equity, or None if equity is unreadable.
        If session-start equity was unreadable, the session stays closed
        (no late recording of SSE).
        """
        day = _utc_day(now)
        try:
            eq: float | None = _positive(equity, "equity")
        except RiskInputError:
            eq = None
        if day != self.session_day:
            self.session_day = day
            self.session_start_equity = eq
            self.trades_today = 0
            self.halted = False
        if eq is not None and self.session_start_equity is not None:
            loss = self.session_start_equity - eq
            limit = self.config.daily_loss_limit_pct * self.session_start_equity / 100.0
            if loss >= limit:  # reaching the limit halts the session
                self.halted = True
        return eq

    def record_entry_accepted(self) -> None:
        """Call after an entry order is accepted; exits do not count."""
        self.trades_today += 1

    def check_entry(
        self,
        now: datetime,
        equity: object,
        broker: BrokerInputs,
        request: EntryRequest,
        open_positions: object,
    ) -> Decision:
        try:
            return self._check(now, equity, broker, request, open_positions)
        except RiskInputError as exc:
            return Decision(False, reason=f"fail closed: {exc}")

    def _check(self, now, equity, broker, request, open_positions) -> Decision:
        cfg = self.config
        eq = self.observe_equity(now, equity)

        # 1. Daily-loss halt flag
        if eq is None:
            return Decision(False, reason="fail closed: equity unreadable")
        if self.session_start_equity is None:
            return Decision(False, reason="fail closed: session-start equity unavailable")
        if self.halted:
            return Decision(False, reason="daily loss limit reached; halted until 00:00 UTC")

        # 2. Trades today
        if self.trades_today >= cfg.max_trades_per_day:
            return Decision(False, reason="MAX_TRADES_PER_DAY reached")

        # 3. Open positions
        if isinstance(open_positions, bool) or not isinstance(open_positions, int):
            raise RiskInputError("open_positions must be an integer")
        if open_positions < 0:
            raise RiskInputError("open_positions must be >= 0")
        if open_positions >= cfg.max_open_positions:
            return Decision(False, reason="MAX_OPEN_POSITIONS reached")

        # 4. Broker leverage readable; effective cap = min(policy, broker)
        broker_lev = _positive(broker.max_leverage, "broker max leverage")
        effective_lev = min(cfg.max_leverage_policy, broker_lev)

        # 5. Stop and target strictly greater than break-even
        be = cfg.break_even_pips
        stop = _positive(request.stop_pips, "stop_pips")
        target = _positive(request.target_pips, "target_pips")
        if stop <= be:
            return Decision(False, reason=f"stop {stop} not > break-even {be}")
        if target <= be:
            return Decision(False, reason=f"target {target} not > break-even {be}")

        # 6. Units = floor_to_lot_step(min(risk units, cap units)).
        #    risk units: risk amount / (stop pips x pip value)  (risk.md section 2)
        #    cap units:  effective cap x equity / price          (risk.md section 3)
        #    Rounding is DOWN only, so risk never exceeds RISK_PER_TRADE_PCT.
        pip_value = _positive(broker.pip_value_per_unit, "pip value per unit")
        lot_step = _positive(broker.lot_step, "lot step")
        min_units = _positive(broker.min_units, "min units")
        price = _positive(request.entry_price, "entry price")
        risk_amount = eq * cfg.risk_per_trade_pct / 100.0
        risk_units = risk_amount / (stop * pip_value)
        cap_units = effective_lev * eq / price
        units = math.floor(min(risk_units, cap_units) / lot_step) * lot_step
        if units < min_units:
            return Decision(False, reason="units below broker minimum after rounding")

        # 7. Post-trade leverage: notional / equity <= effective cap (safety net)
        leverage = units * price / eq
        if leverage > effective_lev:
            return Decision(False, reason=f"leverage {leverage:.4f} > effective cap {effective_lev}")

        return Decision(True, units=units, reason="ok")
