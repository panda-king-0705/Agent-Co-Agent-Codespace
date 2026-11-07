"""Simulated execution and trade-log support for the EUR/USD scalper."""
from __future__ import annotations

import csv
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from .risk import BrokerInputs, EntryRequest, RiskManager
from .settings import Settings
from .signal import Bar, Side, check_exits, signals_for_closes, spread_within_limit

TRADE_FIELDS = ["trade_id", "side", "units", "entry_time", "entry_price", "exit_time", "exit_price", "exit_reason", "pnl_pips", "spread_pips", "commission_pips", "slippage_pips", "net_pnl_pips", "net_pnl_money", "hold_minutes"]

@dataclass(frozen=True)
class SimulatedFill:
    price: float
    spread_pips: float
    commission_pips: float
    slippage_pips: float

@dataclass(frozen=True)
class Trade:
    trade_id: str
    side: Side
    units: float
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    spread_pips: float
    commission_pips: float
    slippage_pips: float
    pnl_pips: float
    net_pnl_pips: float
    net_pnl_money: float
    hold_minutes: float

class CsvTradeLog:
    """Append one complete trade record per closed position."""
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, trade: Trade) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        exists = self.path.exists() and self.path.stat().st_size > 0
        with self.path.open("a", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=TRADE_FIELDS)
            if not exists:
                writer.writeheader()
            writer.writerow({**trade.__dict__, "side": trade.side, "entry_time": trade.entry_time.isoformat(), "exit_time": trade.exit_time.isoformat()})

class SimulatedExecutor:
    """Routes all entries via gates and RiskManager; closes are simulated fills."""
    def __init__(self, settings: Settings, risk: RiskManager, broker_inputs: BrokerInputs,
                 equity: Callable[[], float], log: CsvTradeLog,
                 commission_pips: float | None = None, slippage_pips: float = 0.5):
        self.settings, self.risk, self.broker_inputs = settings, risk, broker_inputs
        self.equity, self.log = equity, log
        # risk.Config is the recorded planning assumption (0.5 pip); explicit override permits modeling.
        self.commission_pips = risk.config.commission_pips if commission_pips is None else commission_pips
        self.slippage_pips = slippage_pips
        self.position: dict | None = None

    def _spread(self, bar: Bar) -> float:
        return self.risk.config.spread_pips if bar.spread_pips is None else float(bar.spread_pips)

    def _fill(self, side: Side, mid: float, spread: float, opening: bool, reason: str = "") -> SimulatedFill:
        half = spread * self.settings.pip_size / 2
        adverse = self.slippage_pips * self.settings.pip_size
        # Long entry buys at ask; long exit sells at bid, with adverse slippage;
        # inverse for short. take-profit has target price, no slippage per spec.
        if opening:
            price = mid + half if side == "long" else mid - half
            price += adverse if side == "long" else -adverse
        else:
            price = mid - half if side == "long" else mid + half
            if reason != "take_profit":
                price += -adverse if side == "long" else adverse
        return SimulatedFill(price, spread, self.commission_pips, 0.0 if reason == "take_profit" and not opening else self.slippage_pips)

    def enter(self, side: Side, bar: Bar, stop_pips: float | None = None, target_pips: float | None = None,
              open_positions: int = 0) -> bool:
        """Apply spread, session/news/max-spread gates, then mandatory risk sizing."""
        if self.position is not None:
            return False
        # Gate failures are closed by default if helper methods or blackout parsing are unavailable.
        from .gates import entry_allowed
        if not entry_allowed(bar.time, self.settings):
            return False
        spread = self._spread(bar)
        if not spread_within_limit(spread, self.settings):
            return False
        stop = self.settings.stop_loss_pips if stop_pips is None else stop_pips
        target = self.settings.take_profit_pips if target_pips is None else target_pips
        decision = self.risk.check_entry(bar.time, self.equity(), self.broker_inputs,
                                         EntryRequest(stop, target, bar.open), open_positions)
        if not decision.allowed:
            return False
        fill = self._fill(side, bar.open, spread, True)
        self.position = {"side": side, "units": decision.units, "entry_time": bar.time,
                         "entry_price": fill.price, "spread": spread,
                         "commission": self.commission_pips, "slippage": fill.slippage_pips}
        self.risk.record_entry_accepted()
        return True

    def exit(self, bar: Bar, reason: str, mid_price: float | None = None) -> Trade | None:
        if self.position is None:
            return None
        p = self.position
        mid = bar.close if mid_price is None else mid_price
        fill = self._fill(p["side"], mid, self._spread(bar), False, reason)
        direction = 1 if p["side"] == "long" else -1
        gross = direction * (fill.price - p["entry_price"]) / self.settings.pip_size
        spread_cost = (p["spread"] + fill.spread_pips) / 2
        commission_cost = p["commission"] + fill.commission_pips
        slip_cost = p["slippage"] + fill.slippage_pips
        net = gross - commission_cost
        # Gross is based on bid/ask and slippage fill prices, so report explicit costs
        # separately and avoid subtracting spread/slippage twice in net P&L.
        money = net * float(self.broker_inputs.pip_value_per_unit) * p["units"]
        trade = Trade(str(uuid.uuid4()), p["side"], p["units"], p["entry_time"], p["entry_price"],
                      bar.time, fill.price, reason, spread_cost, commission_cost, slip_cost,
                      gross, net, money, (bar.time-p["entry_time"]).total_seconds()/60)
        self.log.append(trade)
        self.position = None
        return trade

    def process_bars(self, bars: list[Bar]) -> list[Trade]:
        """Run strategy signals/exits over completed M1 bars; entries fill next open."""
        if len(bars) < 2:
            return []
        closes = [b.close for b in bars]
        signals = signals_for_closes(closes, self.settings)
        fast, slow = __import__("fx_scalper.signal", fromlist=["ema"]).ema(closes, self.settings.ema_fast_period), __import__("fx_scalper.signal", fromlist=["ema"]).ema(closes, self.settings.ema_slow_period)
        trades: list[Trade] = []
        for i, bar in enumerate(bars):
            if self.position is not None:
                p = self.position
                reason = check_exits(p["side"], p["entry_price"], p["entry_time"], bar,
                                     fast[i-1] if i else None, slow[i-1] if i else None,
                                     fast[i], slow[i], False, self.settings)
                if reason:
                    trade = self.exit(bar, reason, bar.open if reason in ("max_hold", "forced_flat") else bar.close)
                    if trade: trades.append(trade)
            if i + 1 < len(bars) and self.position is None and signals[i]:
                if self.enter(signals[i], bars[i+1]):
                    pass
        if self.position is not None:
            trade = self.exit(bars[-1], "forced_flat", bars[-1].close)
            if trade: trades.append(trade)
        return trades
