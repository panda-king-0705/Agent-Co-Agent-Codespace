import csv
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from fx_scalper.executor import CsvTradeLog, SimulatedExecutor
from fx_scalper.risk import BrokerInputs, RiskManager
from fx_scalper.settings import Settings
from fx_scalper.signal import Bar
T=datetime(2026,11,2,14,tzinfo=timezone.utc)
def bar(t=T, price=1.1, spread=1.0): return Bar(t,price,price,price,price,spread)
def make(tmp_path):
    return SimulatedExecutor(Settings(api_token='x',account_id='y',blackout_file=str(tmp_path/'news.json')),
        RiskManager(),BrokerInputs(10,1,1,0.0001),lambda:10000,CsvTradeLog(tmp_path/'trades.csv'))
def enter(e, b=None, **kwargs):
    with patch('fx_scalper.executor.signals_for_closes',return_value=['long']):
        return e.enter('long',b or bar(),history=[bar(T-timedelta(minutes=1))],**kwargs)
def test_signal_cannot_be_bypassed(tmp_path):
    e=make(tmp_path)
    assert not e.enter('long',bar())
    with patch('fx_scalper.executor.signals_for_closes',return_value=[None]):
        assert not e.enter('long',bar(),history=[bar(T-timedelta(minutes=1))])
def test_costs_and_append(tmp_path):
    e=make(tmp_path)
    for _ in range(2):
        assert enter(e)
        t=e.exit(bar(T+timedelta(minutes=1),1.1006),'take_profit')
        assert t.pnl_pips == pytest.approx(6)
        assert t.net_pnl_pips == pytest.approx(6-t.spread_pips-t.commission_pips-t.slippage_pips)
        assert t.commission_pips == 2*e.risk.config.commission_pips
    with (tmp_path/'trades.csv').open() as f:
        rows=list(csv.DictReader(f))
    assert len(rows)==2
    assert all(k in rows[0] for k in ('spread_pips','commission_pips','slippage_pips','net_pnl_money'))
def test_session_spread_risk_and_news(tmp_path):
    e=make(tmp_path)
    assert not enter(e,bar(T.replace(hour=10)))
    assert not enter(e,bar(spread=2))
    assert not enter(e,stop_pips=1,target_pips=1)
    (tmp_path/'news.json').write_text('[{"time":"2026-11-02T14:00:00Z"}]')
    assert not enter(e)
    assert e.position is None
    assert not (tmp_path/'trades.csv').exists()
def test_realized_equity_reaches_risk(tmp_path):
    e=make(tmp_path)
    assert enter(e)
    e.exit(bar(T+timedelta(minutes=1),1.09),'stop_loss')
    assert e.risk.halted
    assert not enter(e,bar(T+timedelta(minutes=2)))
def test_no_signal_no_trades(tmp_path):
    e=make(tmp_path)
    assert e.process_bars([bar(T+timedelta(minutes=i)) for i in range(30)]) == []
