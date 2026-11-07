import csv
from datetime import datetime,timezone
from fx_scalper.executor import CsvTradeLog,SimulatedExecutor
from fx_scalper.risk import BrokerInputs,Config,RiskManager
from fx_scalper.settings import Settings
from fx_scalper.signal import Bar
T=datetime(2026,11,2,14,0,tzinfo=timezone.utc)
def make_executor(tmp_path,commission=0.5):
    s=Settings(api_token="x",account_id="y",blackout_file=str(tmp_path/"none.json")); r=RiskManager(Config(commission_pips=commission,spread_pips=1.0,slippage_pips=0.5))
    b=BrokerInputs(max_leverage=10,lot_step=1,min_units=1,pip_value_per_unit=0.0001)
    return SimulatedExecutor(s,r,b,lambda:10000,CsvTradeLog(tmp_path/"trades.csv"))
def test_fill_costs_and_each_closed_trade_appended(tmp_path):
    e=make_executor(tmp_path); assert e.enter("long",Bar(T,1.1,1.1,1.1,1.1,1.0))
    t=e.exit(Bar(T,1.1006,1.1006,1.1006,1.1006,1.0),"take_profit"); assert t
    with (tmp_path/"trades.csv").open() as f: row=next(csv.DictReader(f))
    for k in ("spread_pips","commission_pips","slippage_pips","net_pnl_pips","net_pnl_money"): assert row[k]
    assert float(row["commission_pips"])==1.0 and float(row["slippage_pips"])==0.5
    e.enter("long",Bar(T,1.1,1.1,1.1,1.1,1.0)); e.exit(Bar(T,1.1,1.1,1.1,1.1,1.0),"max_hold")
    with (tmp_path/"trades.csv").open() as f: assert len(list(csv.DictReader(f)))==2
def test_session_and_spread_gates_reject_orders(tmp_path):
    e=make_executor(tmp_path)
    assert not e.enter("long",Bar(T.replace(hour=10),1.1,1.1,1.1,1.1,1.0))
    assert not e.enter("long",Bar(T,1.1,1.1,1.1,1.1,2.0))
def test_risk_rejection_does_not_open_or_log(tmp_path):
    e=make_executor(tmp_path); assert not e.enter("long",Bar(T,1.1,1.1,1.1,1.1,1.0),stop_pips=1,target_pips=1)
    assert e.position is None and not (tmp_path/"trades.csv").exists()
def test_entry_uses_risk_decision_units(tmp_path):
    e=make_executor(tmp_path); assert e.enter("long",Bar(T,1.1,1.1,1.1,1.1,1.0)); assert e.position["units"]>0
