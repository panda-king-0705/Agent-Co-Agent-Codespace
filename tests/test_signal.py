import pytest
from datetime import datetime, timedelta, timezone

from fx_scalper.settings import Settings
from fx_scalper.signal import (
    Bar,
    bar_price_exit,
    check_exits,
    crossover,
    ema,
    entry_signal,
    max_hold_reached,
    rsi_wilder,
    signals_for_closes,
    spread_within_limit,
    stop_price,
    target_price,
)

S = Settings(api_token="t", account_id="a")  # defaults from spec section 14
T0 = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)
PIP = 0.0001


def bar(t=T0, o=1.1000, h=1.1000, l=1.1000, c=1.1000):
    return Bar(time=t, open=o, high=h, low=l, close=c)


# --- Indicators (section 2) ---

def test_ema_seed_is_sma_then_recursive():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = ema(vals, 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)  # SMA(1,2,3)
    assert out[3] == pytest.approx(3.0)  # 4*0.5 + 2*0.5
    assert out[4] == pytest.approx(4.0)  # 5*0.5 + 3*0.5


def test_ema_short_series_is_all_none():
    assert ema([1.0, 2.0], 3) == [None, None]


def test_rsi_all_gains_is_100_and_all_losses_is_0():
    up = [float(i) for i in range(20)]
    down = [float(-i) for i in range(20)]
    assert rsi_wilder(up, 14)[14] == pytest.approx(100.0)
    assert rsi_wilder(down, 14)[14] == pytest.approx(0.0)


def test_rsi_warmup_is_none():
    out = rsi_wilder([float(i % 3) for i in range(30)], 14)
    assert out[:14] == [None] * 14
    assert out[14] is not None


# --- Entry (section 3) ---

def test_long_signal_on_bullish_cross_with_rsi_in_band():
    assert entry_signal(1.0, 1.0, 1.1, 1.0, 60.0, S) == "long"


def test_short_signal_on_bearish_cross_with_rsi_in_band():
    assert entry_signal(1.0, 1.0, 0.9, 1.0, 40.0, S) == "short"


@pytest.mark.parametrize("rsi", [50.0, 70.0, 49.9, 70.1])
def test_long_rsi_bounds_are_exclusive(rsi):
    # 50 and 70 are the exact bounds (section 3, exclusive); outside values block.
    assert entry_signal(1.0, 1.0, 1.1, 1.0, rsi, S) is None


@pytest.mark.parametrize("rsi", [30.0, 50.0, 29.9, 50.1])
def test_short_rsi_bounds_are_exclusive(rsi):
    assert entry_signal(1.0, 1.0, 0.9, 1.0, rsi, S) is None


def test_no_cross_no_signal_even_with_rsi_in_band():
    # Fast already above slow on both bars: no crossover.
    assert entry_signal(1.1, 1.0, 1.2, 1.0, 60.0, S) is None


def test_cross_requires_fast_at_or_below_slow_on_previous_bar():
    # prev fast == slow counts as <= for a long cross (section 3).
    assert crossover(1.0, 1.0, 1.1, 1.0) == "long"
    # prev fast above slow: not a fresh cross.
    assert crossover(1.01, 1.0, 1.1, 1.0) is None


def test_cross_needs_strict_inequality_on_current_bar():
    assert crossover(0.9, 1.0, 1.0, 1.0) is None  # equal on current bar


def test_missing_indicator_gives_no_signal():
    assert crossover(None, 1.0, 1.1, 1.0) is None
    assert entry_signal(1.0, 1.0, 1.1, 1.0, None, S) is None


def test_signals_for_closes_index_zero_is_none():
    closes = [1.0 + 0.0001 * i for i in range(40)]
    sigs = signals_for_closes(closes, S)
    assert sigs[0] is None
    assert len(sigs) == len(closes)


def test_spread_gate_uses_max_spread_pips():
    assert spread_within_limit(1.5, S) is True   # equal is allowed
    assert spread_within_limit(1.6, S) is False


# --- Exit levels (section 4) ---

def test_stop_and_target_in_pips_long():
    assert stop_price("long", 1.1000, S) == pytest.approx(1.1000 - 4 * PIP)
    assert target_price("long", 1.1000, S) == pytest.approx(1.1000 + 6 * PIP)


def test_stop_and_target_in_pips_short():
    assert stop_price("short", 1.1000, S) == pytest.approx(1.1000 + 4 * PIP)
    assert target_price("short", 1.1000, S) == pytest.approx(1.1000 - 6 * PIP)


def test_stop_hit_by_bar_low_for_long():
    b = bar(l=1.1000 - 4 * PIP, h=1.1000)
    assert bar_price_exit("long", 1.1000, b, S) == "stop_loss"


def test_stop_not_hit_one_tick_away_for_long():
    b = bar(l=1.1000 - 3 * PIP, h=1.1000)
    assert bar_price_exit("long", 1.1000, b, S) is None


def test_target_hit_by_bar_high_for_long():
    b = bar(h=1.1000 + 6 * PIP, l=1.1000)
    assert bar_price_exit("long", 1.1000, b, S) == "take_profit"


def test_stop_first_when_both_levels_in_same_bar():
    # Section 4.2: both inside one bar -> stop assumed first.
    b = bar(h=1.1000 + 7 * PIP, l=1.1000 - 5 * PIP)
    assert bar_price_exit("long", 1.1000, b, S) == "stop_loss"
    b_short = bar(h=1.1000 + 5 * PIP, l=1.1000 - 7 * PIP)
    assert bar_price_exit("short", 1.1000, b_short, S) == "stop_loss"


def test_short_stop_and_target_by_high_and_low():
    assert bar_price_exit("short", 1.1000, bar(h=1.1000 + 4 * PIP, l=1.1000), S) == "stop_loss"
    assert bar_price_exit("short", 1.1000, bar(h=1.1000, l=1.1000 - 6 * PIP), S) == "take_profit"


# --- Max hold (section 4.3) ---

def test_max_hold_edge_at_14_59_vs_15_00():
    entry = T0
    assert max_hold_reached(entry, entry + timedelta(minutes=14, seconds=59), S) is False
    assert max_hold_reached(entry, entry + timedelta(minutes=15), S) is True


# --- Combined exit order (section 4) ---

def test_check_exits_order_stop_beats_max_hold_and_cross():
    late = T0 + timedelta(minutes=20)
    b = bar(t=late, l=1.1000 - 5 * PIP, h=1.1000)
    reason = check_exits("long", 1.1000, T0, b, 1.0, 1.0, 0.9, 1.0, False, S)
    assert reason == "stop_loss"


def test_check_exits_max_hold_before_opposite_cross():
    late = T0 + timedelta(minutes=15)
    b = bar(t=late, l=1.1000, h=1.1000)
    reason = check_exits("long", 1.1000, T0, b, 1.0, 1.0, 0.9, 1.0, False, S)
    assert reason == "max_hold"


def test_check_exits_opposite_cross_exit():
    b = bar(t=T0 + timedelta(minutes=2), l=1.1000, h=1.1000)
    reason = check_exits("long", 1.1000, T0, b, 1.0, 1.0, 0.9, 1.0, False, S)
    assert reason == "opposite_cross"


def test_check_exits_forced_flat_is_last():
    b = bar(t=T0 + timedelta(minutes=2), l=1.1000, h=1.1000)
    assert check_exits("long", 1.1000, T0, b, 1.0, 1.0, 1.0, 1.0, True, S) == "forced_flat"
    assert check_exits("long", 1.1000, T0, b, 1.0, 1.0, 1.0, 1.0, False, S) is None
