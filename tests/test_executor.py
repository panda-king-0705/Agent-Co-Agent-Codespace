import csv
from datetime import datetime, timezone

from fx_scalper.executor import CsvTradeLog, SimulatedExecutor
from fx_scalper.risk import BrokerInputs, Config, RiskManager
from fx_scalper.settings import Settings
from fx_scalper.signal import Bar

T = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)

class AllowAllGatesSettings(Settings):
    pass

def make_executor(tmp_path, commission=0.5):
    settings = Settings(api_token="x", account_id="y", blackout_file=str(tmp_path / "none.json"))
    risk = RiskManager(Config(commission_pips=commission, spread_pips=1.0, slippage_pips=0.5))
    broker = BrokerInputs(max_leverage=10, lot_step=1, min_units=1, pip_value_per_unit=0.0001)
    return SimulatedExecutor(settings, risk, broker, lambda: 10000, CsvTradeLog(tmp_path / "trades.csv")), settings

def test_entry_risk_gate_and_fill_then_csv_has_cost_fields(tmp_path):
    executor, _ = make_executor(tmp_path)
    bar = Bar(T, 1.1, 1.1, 1.1, 1.1, 1.0)
    assert executor.enter("long", bar)
    trade = executor.exit(Bar(T, 1.1006, 1.1006, 1.1006, 1.1006, 1.0), "take_profit")
    assert trade is not None
    with (tmp_path / "trades.csv").open() as f:
        row = next(csv.DictReader(f))
    for key in ("spread_pips", "commission_pips", "slippage_pips", "net_pnl_pips", "net_pnl_money"):
        assert row[key]

def test_rejects_outside_session(tmp_path):
    executor, _ = make_executor(tmp_path)
    bar = Bar(T.replace(hour=10), 1.1, 1.1, 1.1, 1.1, 1.0)
    assert not executor.enter("long", bar)

def test_risk_rejection_does_not_open_or_log(tmp_path):
    executor, _ = make_executor(tmp_path)
    bar = Bar(T, 1.1, 1.1, 1.1, 1.1, 1.0)
    assert not executor.enter("long", bar, stop_pips=1, target_pips=1)
    assert executor.position is None
    assert not (tmp_path / "trades.csv").exists()
