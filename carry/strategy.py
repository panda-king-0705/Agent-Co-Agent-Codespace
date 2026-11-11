"""Daily decision layer of the carry strategy: what may be traded on a given day, and why not.

Everything here is evaluated "as known at time t" through the point-in-time Series loaders.
For each day it ranks the 8 currencies by policy rate, scores each economy, and runs every
top-3 vs bottom-3 pair through the gates, recording each gate's result for the gate log.

Interpretations of the founder's rules (decided where the rule left room, all parameters):
- Fundamental score: each category's last three releases (carry.indicators.category_signal),
  comparing with the previous release instead of a forecast.
- Business cycle (manufacturing PMI is proprietary, so a proxy): expansion when the business
  indicator's last three readings average above zero (industrial production growing) and
  unemployment is less than 0.5 points above its 12-month low. Expansion doubles inflation
  and business; otherwise employment and GDP count double.
- COT: speculators' net position compared with two reports earlier; passes if the
  high-yield currency's rose or the low-yield currency's fell.
- 2-year spread "widened over 20 trading days": spread now above the spread 28 calendar
  days earlier (about 20 trading days).
- Commodities: CAD needs WTI on its side of its 50-day average, AUD and NZD need copper on
  theirs (above when the currency is bought, below when sold).
- Soft inputs: S&P 500 above its 50-day average, or US 10y-2y above zero.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from carry.alfred import load_rows, release_series
from carry.indicators import CATALOG, load_indicator, signal_at
from carry.macro import Series, load_cot, load_daily_candles, load_fred, load_policy_rates
from carry.yields import load_yields, spread_at

CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")
# OANDA quotes each pair with the earlier currency in this list as the base.
BASE_ORDER = ("EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY")
COMMODITY = {"CAD": "wti", "AUD": "copper", "NZD": "copper"}
GATES = ("score", "vix", "cot", "yield_spread", "commodity", "soft")


def instrument_for(a: str, b: str) -> tuple[str, int]:
    """OANDA instrument for currencies a, b and the side that buys a: +1 long, -1 short."""
    if BASE_ORDER.index(a) < BASE_ORDER.index(b):
        return f"{a}_{b}", 1
    return f"{b}_{a}", -1


@dataclass
class MarketData:
    rates: dict[str, Series]
    indicators: dict[tuple[str, str], tuple[Series, int]]  # (currency, category) -> (series, direction)
    unemployment: dict[str, Series]
    vix: Series
    spx: Series
    curve: Series  # US 10y - 2y
    cot: dict[str, Series]
    yields: dict[str, Series]
    commodities: dict[str, Series]  # "wti", "copper"

    @classmethod
    def load(cls, data_dir: str | Path = "data/macro") -> "MarketData":
        d = Path(data_dir)
        indicators = {(i.currency, i.category): (load_indicator(i, d), i.direction) for i in CATALOG}
        unemployment = {c: s for (c, cat), (s, _) in indicators.items() if cat == "employment" and c != "USD"}
        unemployment["USD"] = release_series(load_rows(d / "alfred" / "UNRATE.csv"), "level")
        return cls(
            rates=load_policy_rates(d / "bis_cbpol.csv"),
            indicators=indicators,
            unemployment=unemployment,
            vix=load_fred(d / "fred_VIXCLS.csv"),
            spx=load_daily_candles(d / "SPX500_USD_D.csv"),
            curve=load_fred(d / "fred_T10Y2Y.csv"),
            cot=load_cot(d / "cot"),
            yields=load_yields(d / "yields"),
            commodities={"wti": load_daily_candles(d / "WTICO_USD_D.csv"), "copper": load_daily_candles(d / "XCU_USD_D.csv")},
        )


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def above_average(series: Series, when: datetime, days: int) -> Optional[bool]:
    recent = series.last(when, days)
    if len(recent) < days:
        return None
    return recent[-1].value > _mean([o.value for o in recent])


@dataclass
class Candidate:
    high: str
    low: str
    instrument: str
    side: int  # +1 buy the instrument, -1 sell it
    score_diff: int
    rate_diff: float
    gates: dict[str, bool] = field(default_factory=dict)
    soft_count: int = 0

    @property
    def passed(self) -> bool:
        return all(self.gates.get(g, False) for g in GATES)


@dataclass
class DayContext:
    when: datetime
    ranking: list[str]
    scores: dict[str, int]
    expansion: dict[str, bool]
    candidates: list[Candidate]

    def top(self, n: int = 3) -> list[str]:
        return self.ranking[:n]

    def bottom(self, n: int = 3) -> list[str]:
        return self.ranking[-n:]


class Strategy:
    def __init__(self, data: MarketData, score_threshold: int = 3):
        self.data = data
        self.score_threshold = score_threshold

    # --- carry ranking ---
    def ranking(self, when: datetime) -> list[str]:
        known = {c: self.data.rates[c].asof(when) for c in CURRENCIES if c in self.data.rates}
        if any(v is None for v in known.values()) or len(known) < len(CURRENCIES):
            return []
        return sorted(known, key=lambda c: (-known[c].value, c))

    # --- fundamental score ---
    def expansion(self, currency: str, when: datetime) -> bool:
        business, _ = self.data.indicators[(currency, "business")]
        recent = [o.value for o in business.last(when, 3)]
        growing = len(recent) == 3 and _mean(recent) > 0
        unemployment = self.data.unemployment[currency]
        latest = unemployment.asof(when)
        if latest is None:
            return False
        year = [o.value for o in unemployment.known_at(when) if o.date > latest.date - timedelta(days=365)]
        return growing and latest.value < min(year) + 0.5

    def score(self, currency: str, when: datetime, expansion: bool) -> int:
        doubled = ("inflation", "business") if expansion else ("employment", "gdp")
        total = 0
        for (c, category), (series, direction) in self.data.indicators.items():
            if c == currency:
                total += (2 if category in doubled else 1) * signal_at(series, when, direction)
        return total

    # --- gates ---
    def vix_ok(self, when: datetime) -> bool:
        recent = self.data.vix.last(when, 10)
        if len(recent) < 10:
            return False
        latest = recent[-1].value
        return latest < 20.0 and latest <= 1.2 * _mean([o.value for o in recent])

    def cot_ok(self, high: str, low: str, when: datetime) -> bool:
        def change(currency: str) -> Optional[float]:
            series = self.data.cot.get(currency)
            reports = series.last(when, 3) if series else []
            return reports[-1].value - reports[0].value if len(reports) == 3 else None

        up, down = change(high), change(low)
        return (up is not None and up > 0) or (down is not None and down < 0)

    def yield_ok(self, high: str, low: str, when: datetime) -> bool:
        a, b = self.data.yields.get(high), self.data.yields.get(low)
        if a is None or b is None:
            return False
        now = spread_at(a, b, when)
        before = spread_at(a, b, when - timedelta(days=28))
        return now is not None and before is not None and now > before

    def commodity_ok(self, high: str, low: str, when: datetime) -> bool:
        for currency, wanted_above in ((high, True), (low, False)):
            name = COMMODITY.get(currency)
            if name is None:
                continue
            above = above_average(self.data.commodities[name], when, 50)
            if above is None or above != wanted_above:
                return False
        return True

    def soft_count(self, when: datetime) -> int:
        spx = above_average(self.data.spx, when, 50)
        curve = self.data.curve.asof(when)
        return int(bool(spx)) + int(curve is not None and curve.value > 0)

    # --- the day ---
    def day(self, when: datetime) -> DayContext:
        ranking = self.ranking(when)
        if not ranking:
            return DayContext(when, [], {}, {}, [])
        expansion = {c: self.expansion(c, when) for c in CURRENCIES}
        scores = {c: self.score(c, when, expansion[c]) for c in CURRENCIES}
        vix = self.vix_ok(when)
        soft = self.soft_count(when)
        rates = {c: self.data.rates[c].asof(when).value for c in CURRENCIES}
        candidates = []
        for high in ranking[:3]:
            for low in ranking[-3:]:
                instrument, side = instrument_for(high, low)
                c = Candidate(high, low, instrument, side, scores[high] - scores[low], rates[high] - rates[low])
                c.soft_count = soft
                c.gates = {
                    "score": c.score_diff >= self.score_threshold,
                    "vix": vix,
                    "cot": self.cot_ok(high, low, when),
                    "yield_spread": self.yield_ok(high, low, when),
                    "commodity": self.commodity_ok(high, low, when),
                    "soft": soft >= 1,
                }
                candidates.append(c)
        # Ties: more soft inputs agreeing, then the larger score gap, then the larger rate gap.
        candidates.sort(key=lambda c: (-c.soft_count, -c.score_diff, -c.rate_diff, c.instrument))
        return DayContext(when, ranking, scores, expansion, candidates)


def red_events(data_dir: str | Path = "data/macro", series: Iterable[str] = ("CPIAUCSL", "PAYEMS", "GDPC1")) -> dict[str, list[datetime]]:
    """High-impact release times per currency for the blackout rule.

    Only US releases have exact published times (ALFRED); non-US release times and central
    bank meetings aren't covered yet, so the blackout applies to US events only.
    """
    times = set()
    clocks = {i.key: i for i in CATALOG if i.source == "alfred"}
    for key in series:
        ind = clocks[key]
        for obs in release_series(load_rows(Path(data_dir) / "alfred" / f"{key}.csv"), "level", ind.clock, ind.currency).observations:
            times.add(obs.known_from)
    return {"USD": sorted(times)}
