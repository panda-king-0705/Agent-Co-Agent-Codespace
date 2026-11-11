"""Tests for carry.ta, carry.strategy and carry.backtest, on synthetic data (no files, no network)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from carry.backtest import Backtest, PairData, Params, Trade, entry_signal
from carry.macro import Observation, Series
from carry.strategy import Candidate, DayContext, MarketData, Strategy, instrument_for
from carry.ta import Bar, atr, ema, macd_histogram, pivots, resample, resample_4h, rsi, sma


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def bar(t, o, h, low, c, spread=0.0002):
    return Bar(t, o, h, low, c, spread)


def daily(values, start=date(2024, 1, 1)):
    """A Series of daily values, each known the next midnight UTC."""
    return Series(
        Observation(start + timedelta(days=i), v, datetime.combine(start + timedelta(days=i + 1), datetime.min.time(), tzinfo=timezone.utc))
        for i, v in enumerate(values)
    )


# --- indicators -------------------------------------------------------------------------------


def test_ema_sma_rsi_basics():
    assert ema([1, 2, 3, 4], 2) == [None, 1.5, pytest.approx(2.5), pytest.approx(3.5)]
    assert sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    assert rsi([1, 2, 3, 4, 5], 3)[3:] == [100.0, 100.0]
    falling = rsi([5, 4, 3, 2, 1], 3)
    assert falling[3] == pytest.approx(0.0)
    hist = macd_histogram([float(i) for i in range(60)])
    assert hist[-1] == pytest.approx(0.0, abs=1e-9)  # a straight line: MACD equals its signal


def test_atr_and_resample():
    t0 = utc(2024, 1, 1, 0)
    bars = [bar(t0 + timedelta(hours=h), 1.0, 1.0 + 0.01 * (h + 1), 1.0, 1.0) for h in range(8)]
    four = resample_4h(bars)
    assert [b.time.hour for b in four] == [0, 4]
    assert four[0].high == pytest.approx(1.04) and four[1].high == pytest.approx(1.08)
    assert atr(four, 2)[1] == pytest.approx((0.04 + 0.08) / 2)


def test_daily_resample_joins_sunday_to_monday():
    sunday = utc(2024, 1, 7, 21)  # FX opens Sunday evening UTC
    bars = [bar(sunday + timedelta(hours=h), 1.0, 1.0 + 0.01 * h, 1.0, 1.0) for h in range(30)]
    days = resample(bars, 24)
    assert [d.time for d in days] == [utc(2024, 1, 8), utc(2024, 1, 9)]
    assert days[0].open == 1.0 and days[0].high == pytest.approx(1.26)  # Sunday 21:00 .. Monday 23:00
    assert resample(bars, 4)[0].time == utc(2024, 1, 7, 20)


def test_pivots_confirm_after_width_bars():
    t0 = utc(2024, 1, 1)
    highs = [1, 2, 5, 2, 1, 1, 1]
    bars = [bar(t0 + timedelta(hours=i), h, h, h - 0.5, h) for i, h in enumerate(highs)]
    last_high, _ = pivots(bars, 2)
    assert last_high[3] is None and last_high[4] == 2  # swing at bar 2, known at bar 4


# --- strategy ---------------------------------------------------------------------------------


def test_instrument_for_uses_oanda_base_order():
    assert instrument_for("AUD", "JPY") == ("AUD_JPY", 1)
    assert instrument_for("USD", "EUR") == ("EUR_USD", -1)
    assert instrument_for("NZD", "GBP") == ("GBP_NZD", -1)


def make_data(**overrides):
    flat = daily([1.0] * 80)
    base = dict(
        rates={c: daily([r] * 80) for c, r in zip(("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"), (5, 3, 4, 0.1, 1, 4.5, 3.5, 2))},
        indicators={},
        unemployment={},
        vix=daily([15.0] * 80),
        spx=daily([float(i) for i in range(80)]),
        curve=daily([0.5] * 80),
        cot={},
        yields={},
        commodities={"wti": flat, "copper": flat},
    )
    base.update(overrides)
    return MarketData(**base)


def test_ranking_and_vix_gate():
    s = Strategy(make_data())
    when = utc(2024, 3, 21)  # the day after the synthetic series' last value
    assert s.ranking(when) == ["USD", "CAD", "GBP", "AUD", "EUR", "NZD", "CHF", "JPY"]
    assert s.vix_ok(when)
    spike = Strategy(make_data(vix=daily([15.0] * 70 + [19.0] * 10)))
    assert spike.vix_ok(when)  # 19 < 20 and not 20% above its 10-day average
    jump = Strategy(make_data(vix=daily([12.0] * 79 + [16.0])))
    assert not jump.vix_ok(when)  # 16 is more than 20% above the 10-day average (12.4)
    assert s.soft_count(when) == 2


def test_yield_ranking_and_rate_gap():
    when = utc(2024, 3, 21)
    yields = {"JPY": daily([6.0] * 80), "NZD": daily([5.5] * 70)}  # NZD's last yield is stale
    s = Strategy(make_data(yields=yields), rank_by="yield2y")
    assert s.ranking(when)[:2] == ["JPY", "USD"]  # JPY by its yield; NZD keeps its policy rate
    assert s.ranking(when)[-1] == "CHF"
    gap = Strategy(make_data(), min_rate_gap=3.5)
    gap.expansion = lambda currency, when: False  # no indicator data in this test
    gap.score = lambda currency, when, expansion: 0
    day = gap.day(when)
    passed = {(c.high, c.low): c.gates["rate_gap"] for c in day.candidates}
    assert passed[("USD", "JPY")] and not passed[("GBP", "CHF")]  # 4.9 >= 3.5, 3.0 < 3.5
    with pytest.raises(ValueError):
        Strategy(make_data(), rank_by="other")
    skip = Strategy(make_data(), min_rate_gap=3.5, skip_gates=("rate_gap",))
    skip.expansion, skip.score = gap.expansion, gap.score
    c = next(c for c in skip.day(when).candidates if (c.high, c.low) == ("GBP", "CHF"))
    assert not c.gates["rate_gap"] and "rate_gap" not in c.required  # logged, not required
    with pytest.raises(ValueError):
        Strategy(make_data(), skip_gates=("nope",))


def test_period_summary():
    from carry.backtest import Result

    def tr(day, r):
        t = Trade("AUD_JPY", "AUD", "JPY", 1, utc(2024, 1, day), 100.0, 1.0, 99.0, 103.0, 0.01, 4, 2)
        t.realized_r, t.exit_time = r, utc(2024, 1, day, 12)
        return t

    res = Result(Params(), [tr(1, -1.0), tr(2, 3.0), tr(3, -1.0)], [], [])
    before = res.summary(until=utc(2024, 1, 2))
    after = res.summary(since=utc(2024, 1, 2))
    assert before["trades"] == 1 and before["final_equity"] == pytest.approx(0.99)
    assert after["trades"] == 2 and after["total_r"] == pytest.approx(2.0)
    assert after["max_drawdown"] == pytest.approx(0.01)


def test_cot_yield_and_commodity_gates():
    when = utc(2024, 3, 21)
    cot = {"AUD": daily([1.0, 2.0, 3.0] + [3.0] * 77), "JPY": daily([5.0] * 80)}
    s = Strategy(make_data(cot=cot))
    assert not s.cot_ok("AUD", "JPY", when)  # last three reports flat for both
    s2 = Strategy(make_data(cot={"AUD": daily([1.0] * 78 + [2.0, 3.0]), "JPY": daily([5.0] * 80)}))
    assert s2.cot_ok("AUD", "JPY", when)
    widening = Strategy(make_data(yields={"AUD": daily([4.0 + 0.01 * i for i in range(80)]), "JPY": daily([0.5] * 80)}))
    assert widening.yield_ok("AUD", "JPY", when)
    assert not widening.yield_ok("USD", "JPY", when)  # no USD yield -> fails closed
    rising = daily([1.0 + 0.01 * i for i in range(80)])
    s3 = Strategy(make_data(commodities={"wti": rising, "copper": rising}))
    assert s3.commodity_ok("AUD", "JPY", when)  # copper above its average while buying AUD
    assert not s3.commodity_ok("USD", "AUD", when)  # selling AUD needs copper below
    assert s3.commodity_ok("USD", "JPY", when)  # no commodity currency involved


# --- engine -----------------------------------------------------------------------------------


def trending_pair(n=1200, instrument="AUD_JPY"):
    """Uptrend in 6-hour waves: 3 bars up 0.10, 3 bars down 0.09 (pullbacks deep enough to flip RSI)."""
    t0 = utc(2024, 1, 1)
    bars = []
    price = 100.0
    for i in range(n):
        up = i % 6 < 3
        o, c = price, price + (0.10 if up else -0.09)
        bars.append(bar(t0 + timedelta(hours=i), o, max(o, c) + (0.02 if up else 0.01), min(o, c) - (0.01 if up else 0.02), c, 0.01))
        price = c
    return PairData(instrument, bars)


def test_entry_signal_needs_trend_direction():
    p = trending_pair()
    longs = [i for i in range(900, 1199) if entry_signal(p, i, 1)]
    shorts = [i for i in range(900, 1199) if entry_signal(p, i, -1)]
    assert longs and not shorts  # never against the 4H trend


def test_four_hour_entries_use_daily_trend():
    t0 = utc(2024, 1, 1)
    h1 = trending_pair(n=24 * 300).bars
    p = PairData("AUD_JPY", resample(h1, 4), bar_hours=4, trend_hours=24)
    i = len(p.bars) - 1
    k = p.htf_at(i)
    assert p.bars[i].time + timedelta(hours=4) >= p.htf[k].time + timedelta(days=1)  # trend bar complete
    assert p.htf[k + 1].time + timedelta(days=1) > p.bars[i].time + timedelta(hours=4) if k + 1 < len(p.htf) else True
    assert p.trend(i, 1) is not None and p.trend(i, -1) is None  # daily uptrend
    assert t0 <= p.bars[0].time


class FakeStrategy:
    def __init__(self, data):
        self.data = data


def test_manage_target_stop_and_blackout():
    data = make_data()
    bt = Backtest(FakeStrategy(data), ".", Params(), {"USD": [utc(2024, 1, 2, 13, 30)]})
    t0 = utc(2024, 1, 1, 0)
    mk = lambda **kw: Trade("AUD_JPY", "AUD", "JPY", 1, t0, 100.0, 1.0, 99.0, 103.0, 0.01, 4, 2, best=100.0, **kw)
    p = PairData("AUD_JPY", [bar(t0 + timedelta(hours=i), 100, 100.5, 99.5, 100, 0.0) for i in range(3)] +
                 [bar(t0 + timedelta(hours=3), 100, 103.5, 99.8, 103, 0.0)])
    trade = mk()
    bt._manage(trade, p, 3, p.bars[3].time)  # no strong trend data -> close all at target
    assert trade.remaining == 0 and trade.realized_r == pytest.approx(3.0) and trade.exit_reason == "target"
    both = PairData("AUD_JPY", [bar(t0, 100, 103.5, 98.5, 100, 0.0)])
    trade = mk()
    bt._manage(trade, both, 0, t0)
    assert trade.exit_reason == "stop" and trade.realized_r == pytest.approx(-1.0)  # stop assumed first
    usd = Trade("EUR_USD", "USD", "EUR", -1, t0, 1.10, 0.01, 1.11, 1.07, 0.01, 4, 2, best=1.10)
    p2 = PairData("EUR_USD", [bar(utc(2024, 1, 2, 8), 1.09, 1.095, 1.085, 1.09, 0.0002)])
    bt._manage(usd, p2, 0, p2.bars[0].time)
    assert usd.exit_reason == "blackout"  # US CPI at 13:30, within 6 hours
    assert usd.realized_r == pytest.approx((1.10 - 1.0901) / 0.01)  # bought back at the ask
    off = Backtest(FakeStrategy(data), ".", Params(blackout_hours=0), {"USD": [utc(2024, 1, 2, 13, 30)]})
    usd2 = Trade("EUR_USD", "USD", "EUR", -1, t0, 1.10, 0.01, 1.11, 1.07, 0.01, 4, 2, best=1.10)
    off._manage(usd2, p2, 0, p2.bars[0].time)
    assert usd2.remaining == 1.0 and not off.event_behind(("USD",), utc(2024, 1, 2, 14))  # blackout off


def test_drawdown_brake_and_summary():
    from carry.backtest import Result

    bt = Backtest(FakeStrategy(make_data()), ".", Params(), {})
    c = Candidate("AUD", "JPY", "AUD_JPY", 1, 4, 3.4)
    p = PairData("AUD_JPY", [bar(utc(2024, 1, 1), 100, 100, 100, 100, 0.02)])
    assert bt._open(c, p, 0, utc(2024, 1, 1), 1.0, 0.94, 1.0).risk == 0.005
    t = bt._open(c, p, 0, utc(2024, 1, 1), 1.0, 0.96, 1.0)
    assert t.risk == 0.01 and t.entry == pytest.approx(100.01) and t.stop == pytest.approx(100.01 - 1.5)
    r = Result(Params(), [t], [(utc(2024, 1, 1), 1.0), (utc(2024, 2, 1), 0.92), (utc(2024, 3, 1), 1.1)], [])
    assert r.summary()["max_drawdown"] == pytest.approx(0.08)
    assert isinstance(DayContext(utc(2024, 1, 1), [], {}, {}, []).top(), list)


def test_run_backtest_script_imports():
    import scripts.run_backtest as rb

    assert "trades 0" in rb.line({"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "total_r": 0.0,
                                  "max_drawdown": 0.0, "final_equity": 1.0, "passes": False})
