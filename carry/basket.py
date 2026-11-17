"""Monthly-rebalanced carry basket, marked to market daily (standard library only).

No entry patterns, stops or targets. On the first trading day of each month the 8 currencies
are ranked by carry level (policy rate or 2-year yield, as known at the previous close); the
basket is long the top `per_side` and short the bottom `per_side`, equal weights, 100% of
equity on each side at scale 1. A volatility filter sets the scale each day:

- "none":      always 1.
- "vix":       1 while the last VIX close is below `vix_max`, else 0 (flat).
- "vix_gate":  1 while VIX is below 20 and at most 120% of its 10-day average (the strategy's
               VIX gate), else 0.
- "realized":  1 while the unfiltered basket's `vol_days`-day volatility is at or below its
               own median over the last `median_days` readings, else 0.
- "target":    target_vol / the unfiltered basket's `vol_days`-day annualised volatility,
               capped at `max_scale`; reset only on rebalance days to limit turnover.

Every currency is held against USD through the seven USD pairs (USD's own weight earns
nothing), which is equivalent to holding the crosses. Daily bars are UTC days built from the
H1 files (Sunday evening joined to Monday). Each day's return:

    sum over currencies of weight x (spot return vs USD + policy-rate differential vs USD
    for the calendar days held) - financing markup - trading cost

- Financing markup: `swap_markup` a year on each unit of long-plus-short pair notional
  (half the gross exposure), as in the trade backtester.
- Trading cost: half the pair's median H1 spread that day on every change of weight.
- Weights are held constant between changes; the small daily drift is not re-traded or costed.

Decisions for a day use only what was known at the previous daily close: rates, yields and
VIX as of the midnight after the previous bar's date, and basket returns up to that bar.
Not trading advice.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import median, pstdev
from typing import Optional

from carry.macro import Series
from carry.strategy import CURRENCIES, RANK_BY, carry_levels, vix_calm
from carry.ta import Bar, load_h1

# Currency -> (USD pair, +1 if the currency is the base, -1 if USD is the base).
USD_PAIRS = {
    "EUR": ("EUR_USD", 1), "GBP": ("GBP_USD", 1), "AUD": ("AUD_USD", 1), "NZD": ("NZD_USD", 1),
    "JPY": ("USD_JPY", -1), "CAD": ("USD_CAD", -1), "CHF": ("USD_CHF", -1),
}
FILTERS = ("none", "vix", "vix_gate", "realized", "target")
TRADING_DAYS = 252


@dataclass(frozen=True)
class BasketParams:
    per_side: int = 3
    rank_by: str = "policy"  # policy | yield2y
    vol_filter: str = "none"
    vix_max: float = 20.0
    vol_days: int = 20
    median_days: int = 252
    target_vol: float = 0.08
    max_scale: float = 2.0
    swap_markup: float = 0.01


@dataclass(frozen=True)
class DayBar:
    date: date
    close: float
    spread: float  # median H1 bid/ask spread that day, as a fraction of price


@dataclass(frozen=True)
class Day:
    date: date
    ret: float  # net return on equity
    spot: float
    carry: float  # interest differential less the financing markup
    cost: float
    scale: float
    equity: float
    longs: str
    shorts: str


def daily_bars(bars: Sequence[Bar]) -> list[DayBar]:
    """H1 bars -> UTC-day closes; Sunday's evening bars belong to Monday."""
    out: list[DayBar] = []
    day, close, spreads = None, 0.0, []
    for b in bars:
        d = b.time.date()
        if d.weekday() == 6:
            d += timedelta(days=1)
        if d != day:
            if day is not None:
                out.append(DayBar(day, close, median(spreads) / close))
            day, spreads = d, []
        close = b.close
        spreads.append(b.spread)
    if day is not None:
        out.append(DayBar(day, close, median(spreads) / close))
    return out


def load_prices(candles_dir: str | Path = "data") -> dict[str, list[DayBar]]:
    """Daily bars of the seven USD pairs, by currency."""
    return {c: daily_bars(load_h1(Path(candles_dir) / f"{pair}_H1.csv", pair)) for c, (pair, _) in USD_PAIRS.items()}


def _annual_vol(returns: Sequence[float]) -> float:
    return pstdev(returns) * TRADING_DAYS ** 0.5


class Basket:
    def __init__(self, prices: dict[str, list[DayBar]], rates: dict[str, Series], yields: dict[str, Series],
                 vix: Series, params: BasketParams = BasketParams()):
        if params.rank_by not in RANK_BY:
            raise ValueError(f"rank_by must be one of {RANK_BY}")
        if params.vol_filter not in FILTERS:
            raise ValueError(f"vol_filter must be one of {FILTERS}")
        if not 1 <= params.per_side <= len(CURRENCIES) // 2:
            raise ValueError("per_side must be between 1 and 4")
        self.params = params
        self.rates, self.yields, self.vix = rates, yields, vix
        self.by_date = {c: {b.date: b for b in bars} for c, bars in prices.items()}
        common = set.intersection(*(set(d) for d in self.by_date.values()))
        self.dates = sorted(common)

    def base_weights(self, when: datetime) -> dict[str, float]:
        """+1/n for the top n by carry level, -1/n for the bottom n; empty if a level is unknown."""
        levels = carry_levels(self.rates, self.yields, self.params.rank_by, when)
        if not levels:
            return {}
        ranked = sorted(levels, key=lambda c: (-levels[c], c))
        n = self.params.per_side
        return {**{c: 1.0 / n for c in ranked[:n]}, **{c: -1.0 / n for c in ranked[-n:]}}

    def _gross(self, weights: dict[str, float], prev: date, day: date, when: datetime) -> tuple[float, float]:
        """(spot, interest differential) return of `weights` from `prev`'s close to `day`'s close."""
        usd = self.rates["USD"].asof(when)
        years = (day - prev).days / 365.0
        spot = interest = 0.0
        for c, w in weights.items():
            if c == "USD" or w == 0.0:
                continue
            a, b = self.by_date[c][prev].close, self.by_date[c][day].close
            spot += w * ((b / a - 1.0) if USD_PAIRS[c][1] > 0 else (a / b - 1.0))
            rate = self.rates[c].asof(when)
            if rate is not None and usd is not None:
                interest += w * (rate.value - usd.value) / 100.0 * years
        return spot, interest

    def _scale(self, when: datetime, shadow: list[float], vols: list[float], rebalance: bool, current: float) -> float:
        p = self.params
        if p.vol_filter == "none":
            return 1.0
        if p.vol_filter == "vix":
            latest = self.vix.asof(when)
            return 1.0 if latest is not None and latest.value < p.vix_max else 0.0
        if p.vol_filter == "vix_gate":
            return 1.0 if vix_calm(self.vix, when) else 0.0
        if p.vol_filter == "realized":
            if len(vols) < p.median_days:
                return 0.0
            return 1.0 if vols[-1] <= median(vols[-p.median_days:]) else 0.0
        if not rebalance:  # target: only reset on rebalance days
            return current
        if len(shadow) < p.vol_days:
            return 0.0
        vol = _annual_vol(shadow[-p.vol_days:])
        return min(p.max_scale, p.target_vol / vol) if vol > 0 else 0.0

    def run(self, start: date, end: date, warmup_days: int = 300) -> list[Day]:
        """Daily results for [start, end). The unfiltered basket is tracked from `warmup_days`
        trading days earlier so the volatility filters have history on the first day."""
        p = self.params
        first = next((i for i, d in enumerate(self.dates) if d >= start), len(self.dates))
        begin = max(1, first - warmup_days)
        base: dict[str, float] = {}
        held: dict[str, float] = {}
        month = None
        shadow: list[float] = []  # unfiltered basket's gross daily returns
        vols: list[float] = []  # its rolling vol_days volatility
        scale, equity = 0.0, 1.0
        out: list[Day] = []
        for i in range(begin, len(self.dates)):
            prev, day = self.dates[i - 1], self.dates[i]
            if day >= end:
                break
            when = datetime.combine(prev + timedelta(days=1), time(0), tzinfo=timezone.utc)
            rebalance = (day.year, day.month) != month or not base
            if rebalance:
                month = (day.year, day.month)
                base = self.base_weights(when) or base
            live = day >= start
            if live:
                scale = self._scale(when, shadow, vols, rebalance or not out, scale)
            if base:
                s0, i0 = self._gross(base, prev, day, when)
                shadow.append(s0 + i0)
                if len(shadow) >= p.vol_days:
                    vols.append(_annual_vol(shadow[-p.vol_days:]))
            if not live:
                continue
            target = {c: w * scale for c, w in base.items()}
            cost = sum(
                abs(target.get(c, 0.0) - held.get(c, 0.0)) * self.by_date[c][prev].spread / 2.0
                for c in set(target) | set(held) if c != "USD"
            )
            held = target
            spot, interest = self._gross(held, prev, day, when)
            gross = sum(abs(w) for w in held.values())
            markup = p.swap_markup * gross / 2.0 * (day - prev).days / 365.0
            ret = spot + interest - markup - cost
            equity *= 1.0 + ret
            out.append(Day(day, ret, spot, interest - markup, cost, scale, equity,
                           " ".join(sorted(c for c, w in held.items() if w > 0)),
                           " ".join(sorted(c for c, w in held.items() if w < 0))))
        return out


def summarize(days: Sequence[Day]) -> dict:
    """Return statistics of a run (or of any slice of its days, restarting equity at 1)."""
    if not days:
        return {"days": 0}
    rets = [d.ret for d in days]
    n = len(rets)
    years = max((days[-1].date - days[0].date).days, 1) / 365.25
    equity, peak, worst = 1.0, 1.0, 0.0
    for r in rets:
        equity *= 1.0 + r
        peak = max(peak, equity)
        worst = max(worst, 1.0 - equity / peak)
    mean = sum(rets) / n
    sd = pstdev(rets)
    per_year = n / years
    return {
        "days": n,
        "years": years,
        "annual_return": equity ** (1.0 / years) - 1.0,
        "annual_vol": sd * per_year ** 0.5,
        "sharpe": mean / sd * per_year ** 0.5 if sd > 0 else 0.0,
        "t_stat": mean / (sd / n ** 0.5) if sd > 0 else 0.0,
        "max_drawdown": worst,
        "final_equity": equity,
        "invested": sum(1 for d in days if d.scale > 0) / n,
        "spot_per_year": sum(d.spot for d in days) / years,
        "carry_per_year": sum(d.carry for d in days) / years,
        "cost_per_year": sum(d.cost for d in days) / years,
    }


def by_year(days: Sequence[Day]) -> dict[int, float]:
    out: dict[int, float] = {}
    for d in days:
        out[d.date.year] = (1.0 + out.get(d.date.year, 0.0)) * (1.0 + d.ret) - 1.0
    return out


def write_result(days: Sequence[Day], params: BasketParams, out_dir: str | Path, extra: Optional[dict] = None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "daily.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "ret", "spot", "carry", "cost", "scale", "equity", "longs", "shorts"])
        for d in days:
            w.writerow([d.date.isoformat(), f"{d.ret:.6f}", f"{d.spot:.6f}", f"{d.carry:.6f}", f"{d.cost:.6f}",
                        f"{d.scale:.3f}", f"{d.equity:.6f}", d.longs, d.shorts])
    summary = summarize(days)
    with open(out / "summary.txt", "w", encoding="utf-8") as f:
        for k, v in {**asdict(params), **summary, **(extra or {})}.items():
            f.write(f"{k}: {v}\n")
        for year, r in by_year(days).items():
            f.write(f"year_{year}: {r}\n")
    return summary
