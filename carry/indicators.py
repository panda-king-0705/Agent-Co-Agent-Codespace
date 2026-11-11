"""Official indicators for the fundamental score: one series per category per economy.

Six categories (inflation, employment, gdp, trade, business, sentiment) x 8 economies.
Each release is compared with the previous release, not with a forecast (forecasts are
proprietary; see the project's calendar-data decision).

Two kinds of history:
- US: exact first releases from ALFRED (carry.alfred), usable from their published time.
- Others: today's revised values from official sources (FRED copies of OECD and national
  data, BIS consumer prices, Eurostat, Bank of Canada). Each period is treated as released
  `lag_days` after the period ends, at the next local midnight in the home market. The lags
  are set a few days later than each statistic's usual release so a backtest never sees a
  figure before it was out. Revised figures are slightly cleaner than what traders saw on
  the day, so results from these economies can look somewhat better than reality.

Direction: +1 means a higher reading is good for the currency, -1 means lower is good
(unemployment). Inflation counts as +1: higher inflation points to higher rates.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

from carry.alfred import load_rows, release_series
from carry.clock import to_utc
from carry.macro import Observation, Series

CATEGORIES = ("inflation", "employment", "gdp", "trade", "business", "sentiment")
START = "2005-01"  # enough history for year-on-year changes and trends by 2010

URLS = {
    "fred": "https://fred.stlouisfed.org/graph/fredgraph.csv?id={key}",
    "bis": "https://stats.bis.org/api/v1/data/WS_LONG_CPI/M.{key}.628?format=csv&detail=dataonly&startPeriod=" + START,
    "eurostat": "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/{key}?format=SDMX-CSV&startPeriod=" + START,
    "boc": "https://www.bankofcanada.ca/valet/observations/group/{key}/csv",
}


@dataclass(frozen=True)
class Indicator:
    currency: str
    category: str
    source: str  # alfred | fred | bis | eurostat | boc
    key: str
    transform: str = "level"  # level | pct | yoy | diff
    direction: int = 1
    freq: str = "M"  # M or Q
    lag_days: int = 0  # revised sources: days after period end until treated as released
    clock: Optional[time] = None  # alfred: local release time on the release date

    @property
    def name(self) -> str:
        return f"{self.currency}_{self.category}"

    @property
    def url(self) -> Optional[str]:
        return None if self.source == "alfred" else URLS[self.source].format(key=self.key)

    @property
    def filename(self) -> str:
        return f"{self.key}.csv" if self.source == "alfred" else f"{self.name}.csv"


_I = Indicator
_0830, _0915, _1000 = time(8, 30), time(9, 15), time(10, 0)
_EA = "{}.EA21"  # Eurostat euro area (changing composition; EA21 since Bulgaria joined in 2026)

CATALOG: tuple[Indicator, ...] = (
    _I("USD", "inflation", "alfred", "CPIAUCSL", "yoy", clock=_0830),
    _I("USD", "employment", "alfred", "PAYEMS", "diff", clock=_0830),
    _I("USD", "gdp", "alfred", "GDPC1", "pct", freq="Q", clock=_0830),
    _I("USD", "trade", "alfred", "BOPGSTB", clock=_0830),
    _I("USD", "business", "alfred", "INDPRO", "pct", clock=_0915),
    _I("USD", "sentiment", "alfred", "UMCSENT", clock=_1000),
    _I("EUR", "inflation", "bis", "XM", "yoy", lag_days=20),
    _I("EUR", "employment", "eurostat", _EA.format("une_rt_m/M.SA.TOTAL.PC_ACT.T"), direction=-1, lag_days=35),
    _I("EUR", "gdp", "fred", "CLVMNACSCAB1GQEA19", "pct", freq="Q", lag_days=35),
    _I("EUR", "trade", "eurostat", _EA.format("ei_etea_m/M.BAL_RT.MIO-EUR-SA.EXT_EA21.ET-T"), lag_days=50),
    _I("EUR", "business", "eurostat", _EA.format("sts_inpr_m/M.PRD.B-D.SCA.I21"), "pct", lag_days=50),
    _I("EUR", "sentiment", "eurostat", _EA.format("ei_bsco_m/M.BS-CSMCI.SA.BAL"), lag_days=1),
    _I("GBP", "inflation", "bis", "GB", "yoy", lag_days=25),
    _I("GBP", "employment", "fred", "LRHUTTTTGBM156S", direction=-1, lag_days=50),
    _I("GBP", "gdp", "fred", "NGDPRSAXDCGBQ", "pct", freq="Q", lag_days=50),
    _I("GBP", "trade", "fred", "XTNTVA01GBM664S", lag_days=50),
    _I("GBP", "business", "fred", "GBRPRINTO01GPSAM", lag_days=50),  # already % change
    _I("GBP", "sentiment", "fred", "CSCICP02GBM460S", lag_days=2),
    _I("JPY", "inflation", "bis", "JP", "yoy", lag_days=25),
    _I("JPY", "employment", "fred", "LRHUTTTTJPM156S", direction=-1, lag_days=35),
    _I("JPY", "gdp", "fred", "JPNRGDPEXP", "pct", freq="Q", lag_days=50),
    _I("JPY", "trade", "fred", "XTNTVA01JPM664S", lag_days=25),
    _I("JPY", "business", "fred", "JPNPRINTO01GPSAM", lag_days=35),
    _I("JPY", "sentiment", "fred", "CSCICP02JPM460S", lag_days=5),
    _I("CHF", "inflation", "bis", "CH", "yoy", lag_days=10),
    _I("CHF", "employment", "fred", "LRHUTTTTCHQ156S", direction=-1, freq="Q", lag_days=60),
    _I("CHF", "gdp", "fred", "CLVMNACSCAB1GQCH", "pct", freq="Q", lag_days=65),
    _I("CHF", "trade", "fred", "XTNTVA01CHM664S", lag_days=25),
    _I("CHF", "business", "fred", "PRINTO01CHQ657S", freq="Q", lag_days=60),
    _I("CHF", "sentiment", "fred", "CSCICP02CHQ460S", freq="Q", lag_days=10),
    _I("CAD", "inflation", "bis", "CA", "yoy", lag_days=25),
    _I("CAD", "employment", "fred", "LRHUTTTTCAM156S", direction=-1, lag_days=12),
    _I("CAD", "gdp", "fred", "NGDPRSAXDCCAQ", "pct", freq="Q", lag_days=65),
    _I("CAD", "trade", "fred", "XTNTVA01CAM664S", lag_days=40),
    _I("CAD", "business", "fred", "PRINTO01CAQ657S", freq="Q", lag_days=65),
    # Business Outlook Survey indicator: published early in the quarter it is labelled with,
    # and re-estimated each quarter (so its history is revised more than most).
    _I("CAD", "sentiment", "boc", "BOS_CHART_11", freq="Q", lag_days=-70),
    _I("AUD", "inflation", "bis", "AU", "yoy", freq="Q", lag_days=35),
    _I("AUD", "employment", "fred", "LRHUTTTTAUM156S", direction=-1, lag_days=22),
    _I("AUD", "gdp", "fred", "NGDPRSAXDCAUQ", "pct", freq="Q", lag_days=70),
    _I("AUD", "trade", "fred", "XTNTVA01AUM664S", lag_days=40),
    _I("AUD", "business", "fred", "PRINTO01AUQ657S", freq="Q", lag_days=70),
    _I("AUD", "sentiment", "fred", "CSCICP02AUM460S", lag_days=1),
    _I("NZD", "inflation", "bis", "NZ", "yoy", freq="Q", lag_days=25),
    _I("NZD", "employment", "fred", "LRHUTTTTNZQ156S", direction=-1, freq="Q", lag_days=40),
    _I("NZD", "gdp", "fred", "NAEXKP01NZQ657S", freq="Q", lag_days=85),  # already % change
    _I("NZD", "trade", "fred", "XTNTVA01NZM664S", lag_days=35),
    _I("NZD", "business", "fred", "PRINTO01NZQ657S", freq="Q", lag_days=85),
    _I("NZD", "sentiment", "fred", "NZLCSCICP02STSAQ", freq="Q", lag_days=10),
)


# --- parsing each source into {period start: value} -------------------------------------------


def _period(text: str) -> date:
    text = text.strip()
    if "Q" in text:  # 2026Q3
        year, quarter = text.split("Q")
        return date(int(year), 3 * int(quarter) - 2, 1)
    parts = text.split("-")
    return date(int(parts[0]), int(parts[1]), 1)


def _value(text: str) -> Optional[float]:
    text = text.strip().strip('"')
    if text in ("", ".", "NaN"):
        return None
    return float(text)


def parse_values(source: str, text: str) -> dict[date, float]:
    if source == "boc":  # metadata block, then "OBSERVATIONS" and a date,value table
        text = text.split('"OBSERVATIONS"', 1)[1]
    rows = csv.reader(io.StringIO(text.lstrip("﻿").strip()))
    header = [h.strip() for h in next(rows)]
    if source == "fred":
        date_col, value_col = 0, 1
    elif source == "boc":
        date_col, value_col = 0, 1
    else:  # bis and eurostat both name their columns
        date_col, value_col = header.index("TIME_PERIOD"), header.index("OBS_VALUE")
    values = {}
    for row in rows:
        if len(row) <= max(date_col, value_col):
            continue
        value = _value(row[value_col])
        if value is not None:
            values[_period(row[date_col])] = value
    return values


def _shift_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def to_quarterly(values: dict[date, float]) -> dict[date, float]:
    """Monthly rows that repeat a quarterly figure (BIS for AU, NZ) -> one row per quarter start."""
    out = {}
    for period in sorted(values):
        quarter = date(period.year, 3 * ((period.month - 1) // 3) + 1, 1)
        out[quarter] = values[period]  # the quarter's last available month
    return out


def transformed(values: dict[date, float], transform: str, step_months: int) -> dict[date, float]:
    if transform == "level":
        return dict(values)
    lag = 12 if transform == "yoy" else step_months
    out = {}
    for period, now in values.items():
        before = values.get(_shift_months(period, -lag))
        if before is None:
            continue
        if transform == "diff":
            out[period] = now - before
        elif before != 0:
            out[period] = 100.0 * (now / before - 1.0)
    return out


def revised_known_from(ind: Indicator, period: date) -> datetime:
    step = 3 if ind.freq == "Q" else 1
    period_end = _shift_months(period, step) - timedelta(days=1)
    return to_utc(ind.currency, datetime.combine(period_end + timedelta(days=ind.lag_days + 1), time(0)))


def revised_series(ind: Indicator, text: str) -> Series:
    values = parse_values(ind.source, text)
    if ind.freq == "Q":
        values = to_quarterly(values)
    step = 3 if ind.freq == "Q" else 1
    return Series(Observation(p, v, revised_known_from(ind, p)) for p, v in transformed(values, ind.transform, step).items())


def load_indicator(ind: Indicator, data_dir: str | Path) -> Series:
    """Releases of one indicator, from files written by scripts.fetch_indicators."""
    path = Path(data_dir) / ("alfred" if ind.source == "alfred" else "indicators") / ind.filename
    if ind.source == "alfred":
        return release_series(load_rows(path), ind.transform, ind.clock, ind.currency)
    return revised_series(ind, path.read_text(encoding="utf-8-sig"))


# --- the per-category signal ------------------------------------------------------------------


def category_signal(readings: Sequence[float], direction: int = 1) -> int:
    """+1 / -1 / 0 for one category from its last three releases, oldest first.

    +1 when the latest beat the previous release and the three-reading trend is up
    (latest above the one two releases back); -1 when both point down; else 0.
    `direction` flips the sense for indicators where lower is better.
    """
    if len(readings) < 3:
        return 0
    first, previous, latest = (direction * x for x in readings[-3:])
    if latest > previous and latest > first:
        return 1
    if latest < previous and latest < first:
        return -1
    return 0


def signal_at(series: Series, when: datetime, direction: int = 1) -> int:
    return category_signal([o.value for o in series.last(when, 3)], direction)


def catalog_for(currencies: Iterable[str] | None = None) -> list[Indicator]:
    wanted = set(currencies) if currencies else None
    return [i for i in CATALOG if wanted is None or i.currency in wanted]
