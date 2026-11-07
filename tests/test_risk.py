"""Ready for Board: pytest -q tests/test_risk.py. Not executed here.
Policy: docs/fx-scalper/risk.md. All broker/account values are synthetic fixtures.
"""
import json
from datetime import datetime, timedelta, timezone
import pytest
from fx_scalper.risk_manager import RiskManager, RiskError

NOW = datetime(2026, 11, 8, tzinfo=timezone.utc)
POLICY = dict(risk_per_trade_pct=.5, max_leverage_policy=10, daily_loss_limit_pct=2,
              max_trades_per_day=20, max_open_positions=3, session_reset_utc='00:00',
              spread_pips=1, commission_pips=.5, slippage_pips=.5)

@pytest.fixture
def rm(tmp_path):
    p = tmp_path / 'config.json'
    p.write_text(json.dumps(POLICY))
    r = RiskManager(p, tmp_path / 'state.json')
    r.observe_equity(NOW, 100000)
    return r

def check(rm, equity=100000, now=NOW, **changes):
    args = dict(open_positions=0, broker_max_leverage=50, stop_loss_pips=4,
                target_pips=6, pip_value_per_unit=.03125, lot_step=1,
                min_units=1, unit_notional=1, existing_notional=0)
    args.update(changes)
    return rm.check_entry(now, equity, **args)

def test_size_and_floor(rm):
    assert check(rm) == 4000
    assert check(rm, stop_loss_pips=5, lot_step=1000) == 3000
    with pytest.raises(RiskError):
        check(rm, min_units=4001)

@pytest.mark.parametrize('changes', [dict(unit_notional=251),
    dict(broker_max_leverage=9, unit_notional=250),
    dict(existing_notional=1000000), dict(broker_max_leverage=None)])
def test_leverage(rm, changes):
    with pytest.raises(RiskError):
        check(rm, **changes)

def test_loss_restart_reset(rm):
    rm.record_entry_accepted()
    with pytest.raises(RiskError):
        check(rm, equity=98000)
    r = RiskManager(rm.config_path, rm.state_path)
    assert r.state['session_start_equity'] == '100000'
    assert r.state['trades_today'] == 1
    assert r.close_positions_required
    with pytest.raises(RiskError):
        check(r, equity=101000)
    assert check(r, now=NOW + timedelta(days=1)) == 4000
    assert r.state['trades_today'] == 0
    assert not r.close_positions_required

def test_trade_limit(rm):
    for _ in range(20):
        assert check(rm) == 4000
        rm.record_entry_accepted()
    with pytest.raises(RiskError):
        check(RiskManager(rm.config_path, rm.state_path))

def test_positions(rm):
    assert check(rm, open_positions=2) == 4000
    with pytest.raises(RiskError):
        check(rm, open_positions=3)

@pytest.mark.parametrize('key', ['target_pips', 'stop_loss_pips'])
@pytest.mark.parametrize('distance', [0, 1.9, 2])
def test_break_even(rm, key, distance):
    with pytest.raises(RiskError):
        check(rm, **{key: distance})

def test_reload(rm):
    rm.config_path.write_text(json.dumps(dict(POLICY, commission_pips=5)))
    with pytest.raises(RiskError):
        check(rm)

@pytest.mark.parametrize('equity', [None, 0, float('nan')])
def test_equity(rm, equity):
    with pytest.raises(RiskError):
        check(rm, equity=equity)

def test_corrupt_state(rm):
    rm.state_path.write_text('broken')
    with pytest.raises(RiskError):
        RiskManager(rm.config_path, rm.state_path)
