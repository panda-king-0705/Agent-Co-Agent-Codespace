"""Offline simulated execution; costs are assumptions, not broker quotes."""
from __future__ import annotations
import csv
import uuid
from dataclasses import dataclass, asdict
from datetime import timezone, time
from pathlib import Path
from .risk import EntryRequest
from .signal import signals_for_closes, ema, check_exits, spread_within_limit, stop_price, target_price

@dataclass(frozen=True)
class Trade:
    trade_id: str
    side: str
    units: float
    entry_time: object
    entry_price: float
    exit_time: object
    exit_price: float
    exit_reason: str
    pnl_pips: float
    spread_pips: float
    commission_pips: float
    slippage_pips: float
    net_pnl_pips: float
    net_pnl_money: float
    hold_minutes: float

class CsvTradeLog:
    def __init__(self, path): self.path = Path(path)
    def append(self, trade):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        populated = self.path.exists() and self.path.stat().st_size > 0
        row = asdict(trade)
        for key in ('entry_time', 'exit_time'): row[key] = row[key].isoformat()
        with self.path.open('a', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            if not populated: writer.writeheader()
            writer.writerow(row)

class SimulatedExecutor:
    \"\"\"Entries require completed-bar signal evidence, session gates and risk approval.

    Commission defaults to risk.Config's planning value PER ROUND TRIP (charged once,
    on exit).
    Equity is initial/external equity plus this executor's realized net P&L.
    \"\"\"
    def __init__(self, settings, risk, broker_inputs, equity, log,
                 commission_pips=None, slippage_pips=0.5):
        self.settings, self.risk, self.broker_inputs = settings, risk, broker_inputs
        self.equity, self.log = equity, log
        self.commission_pips = risk.config.commission_pips if commission_pips is None else commission_pips
        self.slippage_pips = slippage_pips
        if self.commission_pips < 0 or slippage_pips < 0: raise ValueError('negative costs')
        self.realized_money = 0.0
        self.position = None
    def _spread(self, bar):
        return self.risk.config.spread_pips if bar.spread_pips is None else float(bar.spread_pips)
    def enter(self, side, bar, stop_pips=None, target_pips=None, open_positions=0, *, history=()):
        from .gates import entry_allowed
        if self.position is not None or side not in ('long', 'short') or not history: return False
        if history[-1].time >= bar.time: return False
        if signals_for_closes([b.close for b in history], self.settings)[-1] != side: return False
        if bar.time.weekday() >= 5 or not entry_allowed(bar.time, self.settings): return False
        spread = self._spread(bar)
        if not 0 <= spread or not spread_within_limit(spread, self.settings): return False
        stop = self.settings.stop_loss_pips if stop_pips is None else stop_pips
        target = self.settings.take_profit_pips if target_pips is None else target_pips
        decision = self.risk.check_entry(bar.time, self.equity()+self.realized_money,
            self.broker_inputs, EntryRequest(stop, target, bar.open), open_positions)
        if not decision.allowed: return False
        direction = 1 if side == 'long' else -1
        price = bar.open + direction*(spread/2+self.slippage_pips)*self.settings.pip_size
        self.position = dict(side=side, units=decision.units, time=bar.time, price=price,
                             mid=bar.open, spread=spread, stop=stop, target=target)
        self.risk.record_entry_accepted()
        return True
    def exit(self, bar, reason, mid_price=None):
        if self.position is None: return None
        p = self.position
        direction = 1 if p['side'] == 'long' else -1
        mid = bar.close if mid_price is None else mid_price
        spread = (p['spread']+self._spread(bar))/2
        slip = self.slippage_pips + (0 if reason == 'take_profit' else self.slippage_pips)
        commission = self.commission_pips
        gross = direction*(mid-p['mid'])/self.settings.pip_size
        net = gross-spread-slip-commission
        price = mid-direction*(self._spread(bar)/2 + (0 if reason == 'take_profit' else self.slippage_pips))*self.settings.pip_size
        money = net*self.broker_inputs.pip_value_per_unit*p['units']
        trade = Trade(str(uuid.uuid4()),p['side'],p['units'],p['time'],p['price'],bar.time,price,
                      reason,gross,spread,commission,slip,net,money,(bar.time-p['time']).total_seconds()/60)
        self.log.append(trade)
        self.realized_money += money
        self.position = None
        self.risk.observe_equity(bar.time,self.equity()+self.realized_money)
        return trade
    def process_bars(self, bars):
        if len(bars) < 2: return []
        closes = [b.close for b in bars]
        signals = signals_for_closes(closes,self.settings)
        fast, slow = ema(closes,self.settings.ema_fast_period), ema(closes,self.settings.ema_slow_period)
        trades = []
        for i, bar in enumerate(bars):
            if self.position is None and i and signals[i-1]:
                self.enter(signals[i-1],bar,history=bars[:i])
            if self.position is None: continue
            p = self.position
            utc = bar.time.astimezone(timezone.utc)
            forced = utc.weekday() >= 5 or (utc.weekday()==4 and utc.time() >= time.fromisoformat(self.settings.forced_flat_friday_utc))
            reason = check_exits(p['side'],p['price'],p['time'],bar,
                fast[i-1] if i else None,slow[i-1] if i else None,fast[i],slow[i],forced,self.settings)
            if not reason: continue
            mid = bar.close
            if reason == 'stop_loss':
                level = stop_price(p['side'],p['price'],self.settings)
                mid = min(bar.open,level) if p['side']=='long' else max(bar.open,level)
            elif reason == 'take_profit': mid = target_price(p['side'],p['price'],self.settings)
            elif reason in ('max_hold','forced_flat'): mid = bar.open
            trades.append(self.exit(bar,reason,mid))
        if self.position is not None: trades.append(self.exit(bars[-1],'forced_flat',bars[-1].close))
        return trades
