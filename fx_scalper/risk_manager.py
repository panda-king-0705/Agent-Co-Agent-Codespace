"""Persistent single-writer risk manager; policy: docs/fx-scalper/risk.md.
Caller serializes submissions, records accepted entries and executes halt exits.
JSON config is reloaded on every check. State must be on durable private storage.
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

class RiskError(ValueError):
    pass

def number(value, positive=True):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise RiskError('numeric input unavailable')
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise RiskError('invalid number') from exc
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise RiskError('number outside valid range')
    return result

class RiskManager:
    def __init__(self, config_path, state_path):
        self.config_path, self.state_path = Path(config_path), Path(state_path)
        self.state = None
        self.failed = False
        self._config()
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text())
                s = self.state
                datetime.strptime(s['day'], '%Y-%m-%d')
                number(s['session_start_equity'])
                assert type(s['halted']) is bool
                assert type(s['trades_today']) is int and s['trades_today'] >= 0
            except Exception as exc:
                raise RiskError('invalid persisted state; manual recovery required') from exc

    def _config(self):
        c = json.loads(self.config_path.read_text())
        for key, ceiling in [('risk_per_trade_pct', '.50'), ('daily_loss_limit_pct', '2'),
                             ('max_leverage_policy', '10'), ('max_trades_per_day', '20'),
                             ('max_open_positions', '3')]:
            c[key] = number(c[key])
            if c[key] > Decimal(ceiling):
                raise RiskError('policy ceiling exceeded: ' + key)
        for key in ['max_trades_per_day', 'max_open_positions']:
            if c[key] != c[key].to_integral_value() or c[key] < 1:
                raise RiskError('invalid integer policy')
        if c['max_leverage_policy'] < 1 or c['session_reset_utc'] != '00:00':
            raise RiskError('invalid leverage or session reset')
        c['break_even'] = sum(number(c[k], False) for k in
                              ['spread_pips', 'commission_pips', 'slippage_pips'])
        number(c['break_even'])
        return c

    def _save(self):
        tmp = None
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', dir=self.state_path.parent,
                                             delete=False) as f:
                tmp = f.name
                json.dump(self.state, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
            fd = os.open(self.state_path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except Exception:
            self.failed = True
            raise
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

    @property
    def close_positions_required(self):
        return self.failed or bool(self.state and self.state['halted'])

    def observe_equity(self, now, equity):
        if self.failed:
            raise RiskError('persistence failed; restart/recovery required')
        c = self._config()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise RiskError('aware timestamp required')
        day = now.astimezone(timezone.utc).date().isoformat()
        eq = number(equity)
        if self.state and day < self.state['day']:
            raise RiskError('clock moved backwards')
        if self.state is None or day > self.state['day']:
            self.state = dict(day=day, session_start_equity=str(eq), halted=False, trades_today=0)
        sse = number(self.state['session_start_equity'])
        if sse - eq >= sse * c['daily_loss_limit_pct'] / 100:
            self.state['halted'] = True
        self._save()
        return c, eq

    def check_entry(self, now, equity, *, open_positions, broker_max_leverage,
                    stop_loss_pips, target_pips, pip_value_per_unit,
                    lot_step, min_units, unit_notional, existing_notional=0):
        """Return Decimal units or raise; notionals are gross account-currency."""
        c, eq = self.observe_equity(now, equity)
        if self.state['halted']:
            raise RiskError('daily halt')
        if self.state['trades_today'] >= c['max_trades_per_day']:
            raise RiskError('trade count limit')
        if type(open_positions) is not int or open_positions < 0:
            raise RiskError('invalid position count')
        if open_positions >= c['max_open_positions']:
            raise RiskError('position limit')
        cap = min(c['max_leverage_policy'], number(broker_max_leverage))
        stop = number(stop_loss_pips)
        if stop <= c['break_even'] or number(target_pips) <= c['break_even']:
            raise RiskError('stop/target must exceed break-even')
        step = number(lot_step)
        raw = eq * c['risk_per_trade_pct'] / 100 / (stop * number(pip_value_per_unit))
        units = (raw / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if units < number(min_units):
            raise RiskError('below broker minimum')
        if (number(existing_notional, False) + units * number(unit_notional)) / eq > cap:
            raise RiskError('post-trade leverage limit')
        return units

    def record_entry_accepted(self):
        if self.failed or self.state is None or self.state['halted']:
            raise RiskError('session unavailable or halted')
        self.state['trades_today'] += 1
        self._save()
