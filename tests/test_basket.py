"""Tests for carry.basket on synthetic data (no files, no network)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from carry.basket import Basket, BasketParams, DayBar, daily_bars, summarize
from carry.macro import Observation, Series
from carry.ta import Bar

START = date(2024, 1, 1)  # a Monday


def weekdays(n, start=START):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def flat_series(value, first=date(2023, 1, 1)):
    return Series([Observation(first, value, datetime(2023, 1, 2, tzinfo=timezone.utc))])


def daily_series(values, dates):
    return Series(
        Observation(d, v, datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
        for d, v in zip(dates, values)
    )


RATES = dict(zip(("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"), (5.0, 3.0, 4.0, 0.1, 1.0, 4.5, 3.5, 2.0)))


def make(n=40, moves=None, vix=None, spread=0.0, **params):
    """Prices flat at 1.0 (100 for JPY) except `moves`: currency -> daily return of its USD pair."""
    dates = weekdays(n)
    prices = {}
    for c in ("EUR", "GBP", "AUD", "NZD", "JPY", "CAD", "CHF"):
        level, bars = (100.0 if c == "JPY" else 1.0), []
        for d in dates:
            bars.append(DayBar(d, level, spread))
            level *= 1.0 + (moves or {}).get(c, 0.0)
        prices[c] = bars
    rates = {c: flat_series(r) for c, r in RATES.items()}
    calm = daily_series([15.0] * 400, [date(2023, 1, 1) + timedelta(days=i) for i in range(400)])
    return Basket(prices, rates, {}, vix if vix is not None else calm, BasketParams(**params)), dates


def test_daily_bars_join_sunday_to_monday():
    sunday = datetime(2024, 1, 7, 22, tzinfo=timezone.utc)
    bars = [Bar(sunday + timedelta(hours=h), 1.0, 1.0, 1.0, 1.0 + 0.01 * h, 0.0002) for h in range(4)]
    days = daily_bars(bars)
    assert [d.date for d in days] == [date(2024, 1, 8)]
    assert days[0].close == pytest.approx(1.03) and days[0].spread == pytest.approx(0.0002 / 1.03)


def test_weights_and_carry_only_return():
    basket, dates = make(swap_markup=0.0)
    w = basket.base_weights(datetime(2024, 1, 2, tzinfo=timezone.utc))
    assert {c for c, x in w.items() if x > 0} == {"USD", "CAD", "GBP"}
    assert {c for c, x in w.items() if x < 0} == {"NZD", "CHF", "JPY"}
    days = basket.run(dates[1], dates[-1] + timedelta(days=1), warmup_days=0)
    first = days[0]  # Monday close to Tuesday close: one day of interest, no price move
    # long CAD, GBP (and USD), short NZD, CHF, JPY, each 1/3; differentials against USD
    expect = ((4.5 - 5) + (4 - 5) - (2 - 5) - (1 - 5) - (0.1 - 5)) / 3 / 100 / 365
    assert first.spot == 0.0 and first.carry == pytest.approx(expect) and first.ret == pytest.approx(expect)
    monday = next(d for d in days if d.date.weekday() == 0)
    assert monday.carry == pytest.approx(3 * expect)  # Friday to Monday holds three days


def test_spot_direction_markup_and_cost():
    basket, dates = make(moves={"JPY": 0.01, "CAD": -0.01}, spread=0.0002)
    days = basket.run(dates[1], dates[-1] + timedelta(days=1), warmup_days=0)
    # USD_JPY up 1%: the yen fell and the short yen leg gains. USD_CAD down 1%: CAD rose and the long gains.
    assert days[1].spot == pytest.approx((1 - 1 / 1.01) / 3 + (1 / 0.99 - 1) / 3)
    assert days[0].cost == pytest.approx(5 * (1 / 3) * 0.0001)  # five non-USD legs opened at half the spread
    assert days[1].cost == 0.0
    no_markup, _ = make(swap_markup=0.0)
    with_markup, _ = make()
    a = no_markup.run(dates[1], dates[3], warmup_days=0)[0].carry
    b = with_markup.run(dates[1], dates[3], warmup_days=0)[0].carry
    assert a - b == pytest.approx(0.01 / 365)  # 1% a year on 100% of pair notional


def test_vix_filter_goes_flat():
    dates = weekdays(40)
    vix = daily_series([15.0] * 10 + [30.0] * 30, [dates[0] - timedelta(days=1)] + dates[:39])
    basket, _ = make(vix=vix, vol_filter="vix")
    days = basket.run(dates[1], dates[-1] + timedelta(days=1), warmup_days=0)
    assert days[0].scale == 1.0 and days[-1].scale == 0.0 and days[-1].ret == 0.0
    assert days[-1].longs == "" and summarize(days)["invested"] < 1.0


def test_target_scale_and_summary():
    basket, dates = make(n=80, moves={"JPY": 0.001}, vol_filter="target", vol_days=5, target_vol=0.08, max_scale=2.0)
    days = basket.run(dates[30], dates[-1] + timedelta(days=1), warmup_days=20)
    assert days[0].scale == 2.0  # a nearly constant return series: volatility far below target, so capped
    s = summarize(days)
    assert s["days"] == len(days) and s["final_equity"] == pytest.approx(days[-1].equity)
    assert s["max_drawdown"] == 0.0 and s["annual_return"] > 0
    with pytest.raises(ValueError):
        make(vol_filter="other")
