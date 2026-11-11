"""Loaders for the carry strategy's non-calendar inputs, each value stamped with when it is known.

Every observation carries `known_from`, the first UTC instant a backtest may use it, and Series
answers "what was known at time t". The release-time assumptions:

- FRED daily series (VIXCLS, T10Y2Y, DGS2): the value for date d is known from d+1 00:00 UTC.
  US markets close by 21:15 UTC, so this is a few hours conservative.
- BIS policy rates (WS_CBPOL): the daily series gives the rate in effect on each date. A rate
  effective on date d is used from d+1 00:00 UTC. Some banks announce the day before the
  effective date (Fed, RBA), so this can lag a decision by up to about a day, but it can't
  run ahead of one (the BoE's changes take effect on the announcement day).
- CFTC Commitments of Traders, legacy futures-only: positions as of Tuesday are published on
  Friday at 15:30 New York time, which is `known_from`. In a week with a US federal holiday
  the CFTC publishes on the following Monday instead; those few reports become usable up to
  three days early here (see COT_RELEASE).
- OANDA daily CFD candles (SPX500_USD, WTICO_USD, XCU_USD): a candle is known once it closes,
  one day after its open time. Its `date` is the trading day it closes on.
"""

from __future__ import annotations

import csv
import io
import math
import zipfile
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from carry.clock import to_utc

BIS_AREAS = {"US": "USD", "XM": "EUR", "GB": "GBP", "JP": "JPY", "CH": "CHF", "CA": "CAD", "AU": "AUD", "NZ": "NZD"}

# CFTC contract codes. USD uses the ICE US Dollar Index future.
COT_CODES = {
    "099741": "EUR",
    "096742": "GBP",
    "097741": "JPY",
    "092741": "CHF",
    "090741": "CAD",
    "232741": "AUD",
    "112741": "NZD",
    "098662": "USD",
}
COT_RELEASE = (timedelta(days=3), time(15, 30))  # Tuesday as-of date -> Friday 15:30 New York


@dataclass(frozen=True)
class Observation:
    date: date
    value: float
    known_from: datetime  # UTC


class Series:
    """Observations sorted by known_from, with point-in-time lookups."""

    def __init__(self, observations: Iterable[Observation]):
        self.observations = sorted(observations, key=lambda o: (o.known_from, o.date))
        self._times = [o.known_from for o in self.observations]

    def __len__(self) -> int:
        return len(self.observations)

    def known_at(self, when: datetime) -> Sequence[Observation]:
        """All observations usable at `when`, oldest first."""
        return self.observations[: bisect_right(self._times, when)]

    def asof(self, when: datetime) -> Optional[Observation]:
        known = self.known_at(when)
        return known[-1] if known else None

    def last(self, when: datetime, n: int) -> list[Observation]:
        """The last `n` observations usable at `when` (fewer if not that many yet)."""
        return list(self.known_at(when)[-n:])


def _midnight_after(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(0), tzinfo=timezone.utc)


def _number(text: str) -> Optional[float]:
    text = text.strip().replace(",", "")
    if text in ("", "."):
        return None
    value = float(text)
    return value if math.isfinite(value) else None  # BIS writes "NaN" for missing days


def parse_fred(lines: Iterable[str]) -> Series:
    """FRED fredgraph CSV: observation_date,<SERIES>. Missing values ('.' or empty) are skipped."""
    reader = csv.reader(lines)
    next(reader)
    observations = []
    for row in reader:
        if len(row) < 2:
            continue
        value = _number(row[1])
        if value is not None:
            day = date.fromisoformat(row[0])
            observations.append(Observation(day, value, _midnight_after(day)))
    return Series(observations)


def parse_policy_rates(lines: Iterable[str]) -> dict[str, Series]:
    """BIS WS_CBPOL CSV (detail=dataonly): FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE -> currency -> Series."""
    by_currency: dict[str, list[Observation]] = defaultdict(list)
    for rec in csv.DictReader(lines):
        currency = BIS_AREAS.get(rec["REF_AREA"].strip())
        value = _number(rec["OBS_VALUE"])
        if currency is None or value is None:
            continue
        day = date.fromisoformat(rec["TIME_PERIOD"].strip())
        by_currency[currency].append(Observation(day, value, _midnight_after(day)))
    return {currency: Series(obs) for currency, obs in by_currency.items()}


@dataclass(frozen=True)
class CotReport:
    currency: str
    as_of: date
    long: int
    short: int

    @property
    def net(self) -> int:
        return self.long - self.short

    @property
    def known_from(self) -> datetime:
        days, clock = COT_RELEASE
        return to_utc("USD", datetime.combine(self.as_of + days, clock))


def parse_cot(lines: Iterable[str]) -> list[CotReport]:
    """Legacy futures-only COT text (annual.txt): non-commercial positions for the 8 currencies."""
    reports = []
    for rec in csv.DictReader(lines):
        currency = COT_CODES.get(rec["CFTC Contract Market Code"].strip())
        if currency is None:
            continue
        reports.append(
            CotReport(
                currency=currency,
                as_of=date.fromisoformat(rec["As of Date in Form YYYY-MM-DD"].strip()),
                long=int(_number(rec["Noncommercial Positions-Long (All)"])),
                short=int(_number(rec["Noncommercial Positions-Short (All)"])),
            )
        )
    return reports


def cot_net_series(reports: Iterable[CotReport]) -> dict[str, Series]:
    """Speculators' (non-commercial) net position per currency; one report per as-of date."""
    unique = {(r.currency, r.as_of): r for r in reports}
    by_currency: dict[str, list[Observation]] = defaultdict(list)
    for r in unique.values():
        by_currency[r.currency].append(Observation(r.as_of, float(r.net), r.known_from))
    return {currency: Series(obs) for currency, obs in by_currency.items()}


def parse_daily_candles(lines: Iterable[str]) -> Series:
    """OANDA daily candle CSV (time,open,high,low,close,...) -> closes, known when the candle closes."""
    observations = []
    for rec in csv.DictReader(lines):
        opened = datetime.fromisoformat(rec["time"].replace("Z", "+00:00"))
        closed = opened + timedelta(days=1)
        observations.append(Observation(closed.date(), float(rec["close"]), closed))
    return Series(observations)


# --- file helpers -------------------------------------------------------------------------------


def _read(path: str | Path):
    return open(path, encoding="utf-8", newline="")


def load_fred(path: str | Path) -> Series:
    with _read(path) as f:
        return parse_fred(f)


def load_policy_rates(path: str | Path) -> dict[str, Series]:
    with _read(path) as f:
        return parse_policy_rates(f)


def load_daily_candles(path: str | Path) -> Series:
    with _read(path) as f:
        return parse_daily_candles(f)


def load_cot(directory: str | Path) -> dict[str, Series]:
    """Read every deacot<year>.zip in `directory` (without extracting) into net-position series."""
    reports: list[CotReport] = []
    for path in sorted(Path(directory).glob("deacot*.zip")):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.lower().endswith(".txt"):
                    with archive.open(name) as raw:
                        reports.extend(parse_cot(io.TextIOWrapper(raw, encoding="utf-8", newline="")))
    return cot_net_series(reports)
