"""Load the Forex Factory economic calendar CSV into UTC-timed, typed releases.

Source: the Hugging Face dataset `Tropstan/Forex_Factory_Calendar` (forex_factory_cache.csv),
research use only, kept in data/ and out of git. Columns: DateTime (ISO with an Asia/Tehran
offset), Currency, Impact, Event, Actual, Forecast, Previous, Detail.

Three quirks of that file shape this loader:

1. Missing release times. About 30-45% of high-impact rows (Non-Farm Payrolls, US CPI, US
   retail sales among them) carry only a date: the scraper stored them at 00:00 Tehran time,
   and the file was then sorted by time, so their slot can't be read back from the row order.
   Such a row gets the event's usual release time in its home market's local clock (8:30 New
   York, 07:00 London, 11:30 Sydney...), learned from that event's rows that do carry a time
   within RECENT_YEARS of the date (all years if none). Local release times don't move with
   daylight saving, so this holds across clock changes. An event that never carries its own
   time borrows the usual time of the event released with it (SIBLINGS). Rows that still
   can't be placed get `time_source == "unknown"` and cover the whole Tehran calendar day
   (see Release.window); so do rows at 23:59:59, which are all-day items (bank holidays,
   meetings).

2. Wrong Tehran offsets from 2023 (see IRAN_NO_DST_FROM).

3. Direction. For some events a lower number is good for the currency (unemployment, jobless
   claims). Each row's Detail carries a "Usual Effect" line; `effect` is +1 when higher is good,
   -1 when lower is good, 0 when there is no consistent effect or none is given.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from carry.clock import to_local, to_utc

CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")

# Event name -> category, first match wins. Case-sensitive on purpose ("ISM" must not match "Tourism").
CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("inflation", r"CPI|Consumer Price|HICP|PCE|PPI|Inflation|GDP Price Index"),
        ("employment", r"Non-Farm|Employment|Unemployment|Claimant|Jobless|Payroll|Earnings|Wage"),
        ("gdp", r"GDP"),
        ("trade", r"Trade Balance|Current Account"),
        ("sentiment", r"Consumer Confidence|Consumer Sentiment|ZEW|Ifo|GfK|Economic Sentiment|Investor Confidence"
                      r"|Consumer Climate|Economic Expectations|Economy Watchers"),
        ("business", r"PMI|ISM|Tankan|KOF|Business Confidence|Ivey|Business Climate|Industrial Production"
                     r"|Manufacturing Index"),
    )
)

IMPACTS = {
    "High Impact Expected": "high",
    "Medium Impact Expected": "medium",
    "Low Impact Expected": "low",
    "Non-Economic": "non-economic",
}

# Events that rarely carry their own time in the file -> an event of the same currency released
# in the same slot. The sibling's times are used when it has more timed rows than the event.
SIBLINGS = {
    ("USD", "CPI y/y"): "Core CPI m/m",
    ("USD", "Unemployment Rate"): "Non-Farm Employment Change",
    ("USD", "Average Hourly Earnings m/m"): "Non-Farm Employment Change",
    ("USD", "CPI m/m"): "Core CPI m/m",
    ("USD", "Retail Sales m/m"): "Core Retail Sales m/m",
    ("USD", "Personal Spending m/m"): "Core PCE Price Index m/m",
    ("USD", "Personal Income m/m"): "Core PCE Price Index m/m",
    ("USD", "Employment Cost Index q/q"): "Unemployment Claims",
    ("USD", "Prelim UoM Inflation Expectations"): "Prelim UoM Consumer Sentiment",
    ("CAD", "Unemployment Rate"): "Employment Change",
    ("CAD", "Overnight Rate"): "BOC Rate Statement",
    ("CAD", "Retail Sales m/m"): "Core Retail Sales m/m",
    ("CAD", "Trimmed CPI y/y"): "CPI m/m",
    ("CAD", "Median CPI y/y"): "CPI m/m",
    ("AUD", "Unemployment Rate"): "Employment Change",
    ("AUD", "Trimmed Mean CPI q/q"): "CPI q/q",
    ("NZD", "Unemployment Rate"): "Employment Change q/q",
    ("NZD", "Retail Sales q/q"): "Core Retail Sales q/q",
    ("GBP", "Unemployment Rate"): "Claimant Count Change",
    ("CHF", "SNB Policy Rate"): "SNB Monetary Policy Assessment",
    ("EUR", "German Flash Services PMI"): "German Flash Manufacturing PMI",
}

# The usual time of an event is the most common local time among its timed rows this close in date.
RECENT_YEARS = 2

# Iran stopped daylight saving from 2023, but the file still labels its old summer window
# (late March to late September) +04:30 in 2023-2025. The wall-clock times are right and the
# offset is wrong: checked against US 8:30 ET releases, which come out an hour early otherwise.
IRAN_NO_DST_FROM = 2023
_TEHRAN = timezone(timedelta(hours=3, minutes=30))
_TEHRAN_SUMMER = timedelta(hours=4, minutes=30)

_UNTIMED = "00:00:00"
_ALL_DAY = "23:59:59"
_MULTIPLIERS = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_NUMBER = re.compile(r"^[<>]?(-?\d+(?:\.\d+)?)([KMBT]?)%*$", re.IGNORECASE)
_EFFECT = re.compile(r"Usual Effect:\s*([^|;]*)")


@dataclass(frozen=True)
class Release:
    time: datetime  # UTC. For "unknown" rows: the start of the Tehran calendar day.
    time_source: str  # "exact", "imputed" or "unknown"
    currency: str
    impact: str  # "high", "medium", "low" or "non-economic"
    event: str
    category: Optional[str]
    actual: Optional[float]
    forecast: Optional[float]
    previous: Optional[float]
    effect: int  # +1 higher is good for the currency, -1 lower is good, 0 no consistent effect

    def window(self) -> tuple[datetime, datetime]:
        """Earliest and latest instant the release may have happened (equal unless unknown)."""
        if self.time_source == "unknown":
            return self.time, self.time + timedelta(days=1)
        return self.time, self.time

    @property
    def known_from(self) -> datetime:
        """First instant a backtest may use the actual value (the end of the window)."""
        return self.window()[1]

    def surprise(self) -> Optional[int]:
        """+1 beat (good for the currency), -1 miss, 0 in line; None if not comparable."""
        if self.actual is None or self.forecast is None or self.effect == 0:
            return None
        diff = self.actual - self.forecast
        return 0 if diff == 0 else (1 if diff > 0 else -1) * self.effect


def parse_value(text: str) -> Optional[float]:
    """Parse '1.2%', '-150K', '3.2B', '<0.10%', '3.94|1.8' (first part). Votes, words -> None."""
    text = text.strip().split("|")[0].strip()
    match = _NUMBER.match(text)
    if not match:
        return None
    return float(match.group(1)) * _MULTIPLIERS.get(match.group(2).upper(), 1.0)


def parse_effect(detail: str) -> int:
    match = _EFFECT.search(detail)
    if not match:
        return 0
    effect = match.group(1)
    if "greater than" in effect or "More hawkish" in effect:
        return 1
    if "less than" in effect:
        return -1
    return 0


def categorize(event: str) -> Optional[str]:
    for name, pattern in CATEGORY_PATTERNS:
        if pattern.search(event):
            return name
    return None


@dataclass
class _Row:
    raw: datetime  # as in the file, with its Tehran offset
    clock: str  # Tehran time of day, HH:MM:SS
    currency: str
    impact: str
    event: str
    actual: str
    forecast: str
    previous: str
    detail: str


def _usual_local_time(history: list[tuple[date, time]], day: date) -> time:
    """Most common local time among timed rows within RECENT_YEARS of `day` (else all of them).

    Ties go to the time seen nearest in date.
    """
    span = timedelta(days=round(365.25 * RECENT_YEARS))
    pool = [item for item in history if abs(item[0] - day) <= span] or history
    counts = Counter(clock for _, clock in pool)
    top = max(counts.values())
    return min((item for item in pool if counts[item[1]] == top), key=lambda item: abs(item[0] - day))[1]


def _place(currency: str, clock: time, day_start: datetime) -> datetime:
    """The UTC instant at local `clock` that falls inside the Tehran day [day_start, +24h)."""
    end = day_start + timedelta(days=1)
    local_day = to_local(currency, day_start).date()
    for offset in (0, 1, -1):
        instant = to_utc(currency, datetime.combine(local_day + timedelta(days=offset), clock))
        if day_start <= instant < end:
            return instant
    return to_utc(currency, datetime.combine(local_day, clock))  # only on a clock-change day


def _resolve_times(rows: list[_Row]) -> list[tuple[datetime, str]]:
    """UTC time and time_source for each row, in order. Rows must be for the 8 CURRENCIES."""
    history: dict[tuple[str, str], list[tuple[date, time]]] = defaultdict(list)
    for row in rows:
        if row.clock not in (_UNTIMED, _ALL_DAY):
            local = to_local(row.currency, row.raw)
            history[(row.currency, row.event)].append((local.date(), local.time()))

    resolved = []
    for row in rows:
        if row.clock not in (_UNTIMED, _ALL_DAY):
            resolved.append((row.raw.astimezone(timezone.utc), "exact"))
            continue
        day_start = row.raw.replace(hour=0, minute=0, second=0).astimezone(timezone.utc)
        own = history.get((row.currency, row.event), [])
        sibling = history.get((row.currency, SIBLINGS.get((row.currency, row.event), "")), [])
        source = sibling if len(sibling) > len(own) else own
        if row.clock == _ALL_DAY or not source:
            resolved.append((day_start, "unknown"))
            continue
        clock = _usual_local_time(source, row.raw.date())
        resolved.append((_place(row.currency, clock, day_start), "imputed"))
    return resolved


def read_rows(lines: Iterable[str]) -> list[_Row]:
    rows = []
    for rec in csv.DictReader(lines):
        raw = datetime.fromisoformat(rec["DateTime"])
        if raw.tzinfo is None:
            raise ValueError(f"DateTime without an offset: {rec['DateTime']!r}")
        if raw.year >= IRAN_NO_DST_FROM and raw.utcoffset() == _TEHRAN_SUMMER:
            raw = raw.replace(tzinfo=_TEHRAN)  # same wall-clock time, correct offset
        rows.append(
            _Row(
                raw=raw,
                clock=raw.strftime("%H:%M:%S"),
                currency=rec["Currency"].strip(),
                impact=IMPACTS.get(rec["Impact"].strip(), "unknown"),
                event=rec["Event"].strip(),
                actual=rec["Actual"],
                forecast=rec["Forecast"],
                previous=rec["Previous"],
                detail=rec.get("Detail") or "",
            )
        )
    return rows


def parse_calendar(lines: Iterable[str], currencies: Iterable[str] = CURRENCIES) -> list[Release]:
    """Releases for `currencies` (a subset of CURRENCIES), sorted by UTC time."""
    keep = set(currencies)
    unknown = keep - set(CURRENCIES)
    if unknown:
        raise ValueError(f"unsupported currencies: {sorted(unknown)}")
    rows = [row for row in read_rows(lines) if row.currency in keep]
    releases = [
        Release(
            time=when,
            time_source=source,
            currency=row.currency,
            impact=row.impact,
            event=row.event,
            category=categorize(row.event),
            actual=parse_value(row.actual),
            forecast=parse_value(row.forecast),
            previous=parse_value(row.previous),
            effect=parse_effect(row.detail),
        )
        for row, (when, source) in zip(rows, _resolve_times(rows))
    ]
    releases.sort(key=lambda r: (r.time, r.currency, r.event))
    return releases


def load_calendar(path: str | Path, currencies: Iterable[str] = CURRENCIES) -> list[Release]:
    with open(path, encoding="utf-8", newline="") as f:
        return parse_calendar(f, currencies)
