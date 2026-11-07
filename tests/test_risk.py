"""Tests for the public fx_scalper.risk API; not run in this environment."""
import json
from datetime import datetime, timedelta, timezone
import pytest
from fx_scalper.risk import RiskManager, RiskError, Config, BrokerInputs, EntryRequest

NOW=datetime(2026,11,8,tzinfo=timezone.utc)
POLICY=dict(risk_per_trade_pct=.5,max_leverage_policy=10,daily_loss_limit_pct=2,
 max_trades_per_day=20,max_open_positions=3,session_reset_utc="00:00",
 spread_pips=1,commission_pips=.5,slippage_pips=.5)
@pytest.fixture
def rm(tmp_path):
 p=tmp_path/"config.json"; p.write_text(json.dumps(POLICY))
 r=RiskManager(config_path=p,state_path=tmp_path/"state.json")
 r.observe_equity(NOW,100000); return r
def check(rm,equity=100000,now=NOW,**changes):
 b=dict(max_leverage=50,lot_step=1,min_units=1,pip_value_per_unit=.03125); b.update(changes.pop("broker",{}))
 q=dict(stop_pips=4,target_pips=6,entry_price=1); q.update(changes.pop("request",{}))
 return rm.check_entry(now,equity,BrokerInputs(**b),EntryRequest(**q),changes.pop("open_positions",0))

def test_real_api_sizing_and_floor(rm):
 assert check(rm).units==4000
 assert check(rm,request={"stop_pips":5},broker={"lot_step":1000}).units==3000
 assert not check(rm,broker={"min_units":4001}).allowed

def test_cap_limited_rule_unchanged(rm):
 # Cap binds; expected sizing remains the T-367 min(risk, cap) rule.
 assert check(rm,request={"entry_price":100}).units==1000

def test_durable_halt_and_restart(rm,tmp_path):
 rm.record_entry_accepted(); assert not check(rm,equity=98000).allowed
 r=RiskManager(config_path=rm.config_path,state_path=rm.state_path)
 assert r.session_start_equity==100000 and r.trades_today==1 and r.close_positions_required
 assert not check(r,equity=101000).allowed
 assert check(r,now=NOW+timedelta(days=1)).allowed
 assert r.trades_today==0 and not r.close_positions_required

def test_trade_count_and_positions(rm):
 for _ in range(20):
  assert check(rm).allowed; rm.record_entry_accepted()
 assert not check(rm).allowed
 assert not check(rm,open_positions=3).allowed

def test_config_reloaded_and_validated(rm):
 rm.config_path.write_text(json.dumps(dict(POLICY,commission_pips=5)))
 assert not check(rm).allowed

def test_corrupt_state_and_backward_clock(rm):
 rm.state_path.write_text("broken")
 with pytest.raises(RiskError): RiskManager(config_path=rm.config_path,state_path=rm.state_path)
 with pytest.raises(RiskError): rm.observe_equity(NOW-timedelta(days=1),100000)

def test_fail_closed_inputs(rm):
 assert not check(rm,equity=None).allowed
 assert not check(rm,broker={"max_leverage":None}).allowed
