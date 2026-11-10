"""FX risk policy enforcement; persistence and config contract from risk.md §§5,8.

ASSUMPTION: commission_pips = 0.5 is the risk.md section 1 planning value.
"""
from __future__ import annotations
import json, math, os, tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
RISK_PER_TRADE_PCT=0.50
MAX_LEVERAGE_POLICY=10.0
DAILY_LOSS_LIMIT_PCT=2.00
MAX_TRADES_PER_DAY=20
MAX_OPEN_POSITIONS=3
SPREAD_PIPS=1.0
COMMISSION_PIPS=0.5
SLIPPAGE_PIPS=0.5

class RiskInputError(ValueError): pass
class RiskError(RiskInputError): pass

@dataclass(frozen=True)
class Config:
    risk_per_trade_pct: float=RISK_PER_TRADE_PCT
    max_leverage_policy: float=MAX_LEVERAGE_POLICY
    daily_loss_limit_pct: float=DAILY_LOSS_LIMIT_PCT
    max_trades_per_day: int=MAX_TRADES_PER_DAY
    max_open_positions: int=MAX_OPEN_POSITIONS
    spread_pips: float=SPREAD_PIPS
    commission_pips: float=COMMISSION_PIPS
    slippage_pips: float=SLIPPAGE_PIPS
    session_reset_utc: str="00:00"
    @property
    def break_even_pips(self) -> float:
        return self.spread_pips + self.commission_pips + self.slippage_pips

@dataclass(frozen=True)
class BrokerInputs:
    max_leverage: float|None
    lot_step: float|None
    min_units: float|None
    pip_value_per_unit: float|None
@dataclass(frozen=True)
class EntryRequest:
    stop_pips: float
    target_pips: float
    entry_price: float
@dataclass(frozen=True)
class Decision:
    allowed: bool
    units: float=0.0
    reason: str=""

def _number(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)): raise RiskInputError(f"{name} is not a number")
    v=float(value)
    if not math.isfinite(v): raise RiskInputError(f"{name} is not finite")
    return v
def _positive(value,name):
    v=_number(value,name)
    if v<=0: raise RiskInputError(f"{name} must be > 0")
    return v
def _utc_day(now):
    if not isinstance(now,datetime) or now.tzinfo is None: raise RiskInputError("timestamp must be timezone-aware")
    return now.astimezone(timezone.utc).date()

def _validate_config(c):
    ranges={"risk_per_trade_pct":.50,"daily_loss_limit_pct":2.0,"max_leverage_policy":10.0,
            "max_trades_per_day":20,"max_open_positions":3}
    for k, ceiling in ranges.items():
        v=_positive(getattr(c,k),k)
        if v>ceiling: raise RiskError(f"policy ceiling exceeded: {k}")
        if k in ("max_trades_per_day","max_open_positions") and (not isinstance(getattr(c,k),int) or isinstance(getattr(c,k),bool)):
            raise RiskError(f"{k} must be an integer")
        if k=="max_leverage_policy" and v<1: raise RiskError("invalid leverage")
    for k in ("spread_pips","commission_pips","slippage_pips"):
        v=_number(getattr(c,k),k)
        if v<0: raise RiskError(f"{k} must be >= 0")
    if c.session_reset_utc!="00:00": raise RiskError("session_reset_utc must be 00:00")

def _config_from_file(path):
    try:
        data=json.loads(Path(path).read_text(encoding="utf-8"))
        allowed={f.name for f in __import__('dataclasses').fields(Config)}
        if not isinstance(data,dict) or set(data)!=allowed: raise RiskError("config keys missing or unexpected")
        return Config(**data)
    except RiskError: raise
    except Exception as exc: raise RiskError("config unreadable or invalid") from exc

class RiskManager:
    """State is atomic/durable; caller serializes operations and performs halt exits."""
    def __init__(self, config:Config|None=None, *, config_path=None, state_path=None):
        self.config=config or Config()
        self.config_path=Path(config_path) if config_path is not None else None
        self.state_path=Path(state_path) if state_path is not None else None
        self.session_day=None; self.session_start_equity=None; self.trades_today=0; self.halted=False
        self.failed=False
        _validate_config(self.config if self.config_path is None else _config_from_file(self.config_path))
        if self.state_path is not None and self.state_path.exists(): self._load()
    def _current_config(self):
        c=_config_from_file(self.config_path) if self.config_path is not None else self.config
        _validate_config(c); return c
    def _load(self):
        try:
            s=json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(s,dict) or set(s)!={"day","session_start_equity","trades_today","halted"}: raise ValueError()
            day=date.fromisoformat(s["day"])
            eq=_positive(s["session_start_equity"],"session_start_equity")
            if type(s["trades_today"]) is not int or s["trades_today"]<0 or type(s["halted"]) is not bool: raise ValueError()
            self.session_day=day; self.session_start_equity=eq; self.trades_today=s["trades_today"]; self.halted=s["halted"]
        except Exception as exc: raise RiskError("invalid persisted state; manual recovery required") from exc
    def _save(self):
        if self.state_path is None: return
        tmp=None
        try:
            self.state_path.parent.mkdir(parents=True,exist_ok=True)
            payload=json.dumps({"day":self.session_day.isoformat(),"session_start_equity":self.session_start_equity,"trades_today":self.trades_today,"halted":self.halted})
            with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=self.state_path.parent,delete=False) as f:
                tmp=f.name; f.write(payload); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,self.state_path); tmp=None
            fd=os.open(self.state_path.parent,os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        except Exception as exc:
            self.failed=True
            raise RiskError("persistence failed; restart/recovery required") from exc
        finally:
            if tmp and os.path.exists(tmp): os.unlink(tmp)
    @property
    def close_positions_required(self): return self.failed or self.halted
    def observe_equity(self,now,equity):
        if self.failed: raise RiskError("persistence failed; restart/recovery required")
        c=self._current_config(); day=_utc_day(now); eq=_positive(equity,"equity")
        if self.session_day is not None and day<self.session_day: raise RiskError("clock moved backwards")
        if self.session_day is None or day>self.session_day:
            self.session_day=day; self.session_start_equity=eq; self.trades_today=0; self.halted=False
        if self.session_start_equity-eq>=self.session_start_equity*c.daily_loss_limit_pct/100: self.halted=True
        self._save(); return eq
    def record_entry_accepted(self):
        if self.failed or self.session_day is None or self.halted: raise RiskError("session unavailable or halted")
        self.trades_today+=1; self._save()
    def check_entry(self,now,equity,broker,request,open_positions):
        try: return self._check(now,equity,broker,request,open_positions)
        except RiskInputError as exc: return Decision(False,reason=f"fail closed: {exc}")
    def _check(self,now,equity,broker,request,open_positions):
        cfg=self._current_config() # reload for each entry check
        eq=self.observe_equity(now,equity)
        if self.halted: return Decision(False,reason="daily loss limit reached; halted until 00:00 UTC")
        if self.trades_today>=cfg.max_trades_per_day: return Decision(False,reason="MAX_TRADES_PER_DAY reached")
        if isinstance(open_positions,bool) or not isinstance(open_positions,int) or open_positions<0: raise RiskInputError("open_positions must be a non-negative integer")
        if open_positions>=cfg.max_open_positions: return Decision(False,reason="MAX_OPEN_POSITIONS reached")

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
