"""Boundary tests for fx_scalper/risk.py (docs/fx-scalper/risk.md).

Numbers are chosen so binary floating point is exact (e.g. pip value 1/32),
so each boundary is tested at its exact value, not an approximation.
Test inputs are illustrative; they are not broker values.
"""

import math
from datetime import datetime, timezone

import pytest

from fx_scalper.risk import (
    BrokerInputs,
    Config,
    EntryRequest,
    RiskManager,
)

T0 = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)
EQUITY = 100000.0
PIP_VALUE = 0.03125  # 1/32, exact in binary
BROKER_OK = BrokerInputs(max_leverage=50.0, lot_step=1.0, min_units=1.0, pip_value_per_unit=PIP_VALUE)
REQ = EntryRequest(stop_pips=4.0, target_pips=6.0, entry_price=1.0)


def fresh(equity=EQUITY, now=T0, **cfg):
    rm = RiskManager(Config(**cfg))
    rm.observe_equity(now, equity)
    return rm


def check(rm, equity=EQUITY, broker=BROKER_OK, req=REQ, open_positions=0, now=T0):
    return rm.check_entry(now, equity, broker, req, open_positions)


# --- Sizing: RISK_PER_TRADE_PCT 0.50 of equity ---------------------------

def test_sizing_risks_exactly_half_percent_of_equity():
    # risk 500 / (4 pips x 0.03125) = 4000 units
    d = check(fresh())
    assert d.allowed and d.units == 4000.0


def test_sizing_rounds_down_to_lot_step_never_up():
    # stop 5: 500 / (5 x 0.03125) = 3200; lot step 1000 -> 3000 (not 4000)
    b = BrokerInputs(50.0, 1000.0, 1000.0, PIP_VALUE)
    d = check(fresh(), broker=b, req=EntryRequest(5.0, 6.0, 1.0))
    assert d.allowed and d.units == 3000.0


def test_sizing_below_broker_minimum_is_rejected():
    b = BrokerInputs(50.0, 1.0, 4001.0, PIP_VALUE)  # min 4001 > 4000
    d = check(fresh(), broker=b)
    assert not d.allowed


def test_sizing_at_broker_minimum_is_allowed():
    b = BrokerInputs(50.0, 1.0, 4000.0, PIP_VALUE)
    assert check(fresh(), broker=b).allowed


# --- Leverage: min(MAX_LEVERAGE_POLICY 10, broker) ----------------------

def test_leverage_exactly_10_is_allowed():
    # notional 4000 x 250 = 1,000,000; / 100,000 = 10.0
    d = check(fresh(), req=EntryRequest(4.0, 6.0, 250.0))
    assert d.allowed


def test_leverage_just_above_10_is_rejected():
    d = check(fresh(), req=EntryRequest(4.0, 6.0, 250.0001))
    assert not d.allowed


def test_policy_cap_applies_when_broker_allows_more():
    # broker 50 -> effective min(10, 50) = 10; 260 gives 10.4
    d = check(fresh(), req=EntryRequest(4.0, 6.0, 260.0))
    assert not d.allowed


def test_broker_cap_applies_when_lower_than_policy():
    # broker 9.5 -> effective 9.5; leverage 10.0 is rejected
    b = BrokerInputs(9.5, 1.0, 1.0, PIP_VALUE)
    assert not check(fresh(), broker=b, req=EntryRequest(4.0, 6.0, 250.0)).allowed


def test_broker_cap_at_exactly_10_is_allowed():
    b = BrokerInputs(10.0, 1.0, 1.0, PIP_VALUE)
    assert check(fresh(), broker=b, req=EntryRequest(4.0, 6.0, 250.0)).allowed


# --- Daily loss stop: 2.00% of session-start equity, 00:00 UTC reset ----

def test_daily_loss_at_exactly_2_percent_halts():
    rm = fresh()  # SSE 100000, limit 2000
    rm.observe_equity(T0, 98000.0)  # loss == 2000
    assert rm.halted
    assert not check(rm, equity=98000.0).allowed


def test_daily_loss_just_under_2_percent_does_not_halt():
    rm = fresh()
    rm.observe_equity(T0, 98000.5)  # loss 1999.5
    assert not rm.halted
    assert check(rm, equity=98000.5).allowed


def test_halt_does_not_clear_on_recovery_within_session():
    rm = fresh()
    rm.observe_equity(T0, 98000.0)
    assert check(rm, equity=100000.0).allowed is False
    assert rm.halted


def test_halt_clears_at_00_00_utc():
    rm = fresh(now=datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc))
    rm.observe_equity(datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc), 98000.0)
    assert rm.halted
    d = check(rm, equity=98000.0, now=datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc))
    assert d.allowed and not rm.halted


def test_session_still_halted_one_second_before_reset():
    rm = fresh(now=datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc))
    rm.observe_equity(datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc), 98000.0)
    d = check(rm, equity=98000.0, now=datetime(2026, 11, 8, 23, 59, 59, tzinfo=timezone.utc))
    assert not d.allowed and rm.halted


def test_session_start_equity_resets_at_00_00_utc():
    rm = fresh(now=datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc))
    rm.observe_equity(datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc), 90000.0)
    assert rm.session_start_equity == 90000.0


# --- MAX_TRADES_PER_DAY = 20 --------------------------------------------

def test_19_trades_today_still_allows_entry():
    rm = fresh()
    for _ in range(19):
        rm.record_entry_accepted()
    assert check(rm).allowed


def test_20_trades_today_blocks_entry():
    rm = fresh()
    for _ in range(20):
        rm.record_entry_accepted()
    assert not check(rm).allowed


def test_trade_counter_resets_at_00_00_utc():
    rm = fresh(now=datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc))
    for _ in range(20):
        rm.record_entry_accepted()
    assert check(rm, now=datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)).allowed


# --- MAX_OPEN_POSITIONS = 3 ----------------------------------------------

def test_2_open_positions_allows_entry():
    assert check(fresh(), open_positions=2).allowed


def test_3_open_positions_blocks_entry():
    assert not check(fresh(), open_positions=3).allowed


# --- Break-even: stop and target strictly greater than 2.0 pips ----------
# break_even = 1.0 spread + 0.5 commission (ASSUMPTION) + 0.5 slippage = 2.0

def test_break_even_is_2_pips_with_planning_values():
    assert Config().break_even_pips == 2.0


def test_stop_equal_to_break_even_is_rejected():
    assert not check(fresh(), req=EntryRequest(2.0, 6.0, 1.0)).allowed


def test_stop_just_above_break_even_is_allowed():
    assert check(fresh(), req=EntryRequest(2.0001, 6.0, 1.0)).allowed


def test_target_equal_to_break_even_is_rejected():
    assert not check(fresh(), req=EntryRequest(4.0, 2.0, 1.0)).allowed


def test_target_just_above_break_even_is_allowed():
    assert check(fresh(), req=EntryRequest(4.0, 2.0001, 1.0)).allowed


# --- Fail closed on unreadable inputs ------------------------------------

@pytest.mark.parametrize("equity", [None, "abc", 0.0, -5.0, math.nan, math.inf, True])
def test_unreadable_equity_fails_closed(equity):
    rm = fresh()
    assert not check(rm, equity=equity).allowed


def test_unrecorded_session_start_equity_fails_closed():
    rm = RiskManager()
    rm.observe_equity(T0, None)  # SSE unreadable at session start
    assert not check(rm).allowed


@pytest.mark.parametrize(
    "broker",
    [
        BrokerInputs(None, 1.0, 1.0, PIP_VALUE),
        BrokerInputs(0.0, 1.0, 1.0, PIP_VALUE),
        BrokerInputs(50.0, None, 1.0, PIP_VALUE),
        BrokerInputs(50.0, 1.0, None, PIP_VALUE),
        BrokerInputs(50.0, 1.0, 1.0, None),
        BrokerInputs(math.nan, 1.0, 1.0, PIP_VALUE),
    ],
)
def test_unreadable_broker_inputs_fail_closed(broker):
    assert not check(fresh(), broker=broker).allowed


def test_naive_timestamp_fails_closed():
    rm = fresh()
    d = rm.check_entry(datetime(2026, 11, 8, 9, 0), EQUITY, BROKER_OK, REQ, 0)
    assert not d.allowed


def test_non_integer_open_positions_fails_closed():
    assert not check(fresh(), open_positions=1.5).allowed
