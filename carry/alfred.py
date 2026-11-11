"""ALFRED (archival FRED) vintages: what each official release said on the day it came out.

ALFRED keeps every revision of a FRED series as rows (date, realtime_start, realtime_end,
value): the value for period `date` as published from realtime_start to realtime_end.
From those rows this module rebuilds each release as it was first seen:

- A period's release date is the first realtime_start of any row for that period.
- The indicator for that release (level, % change, year-on-year %, difference) is
  computed from the vintage in force on the release date, so the previous period's
  value is the one traders saw that day, not today's revised figure.
- Periods that first appear in the series' first vintage are dropped: they are history
  loaded when ALFRED started tracking the series, not releases.

When a release becomes usable (`known_from`): ALFRED dates are US calendar dates and
non-US series reach FRED after their national release, sometimes days later. So by
default a release is usable from the next midnight in New York after its ALFRED date,
which can lag the real release but never runs ahead of it. A series whose release time
is known and published on its ALFRED date (US BLS/BEA/Fed data) can pass `clock` and
`currency`, and is usable from that local time on the release date.

The API needs a free key (FRED_API_KEY, from the environment or the repo's .env). The
key is sent only to api.stlouisfed.org and never appears in errors or output.
"""

from __future__ import annotations

import csv
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

from carry.clock import to_utc
from carry.macro import Observation, Series
from fx_scalper.settings import DOTENV_PATH, read_dotenv

ENV_FRED_KEY = "FRED_API_KEY"
API_ROOT = "https://api.stlouisfed.org/fred/"
EARLIEST = "1776-07-04"  # ALFRED's "all real-time periods" bounds
LATEST = "9999-12-31"
PAGE_LIMIT = 100_000
USER_AGENT = "agentco-fx-research/1.0"
TIMEOUT_SECONDS = 120
CSV_HEADER = ["date", "realtime_start", "realtime_end", "value"]

TRANSFORMS = ("level", "pct", "yoy", "diff")


class AlfredError(RuntimeError):
    """Raised for API or key problems. Messages never contain the API key."""


def api_key(env: Mapping[str, str] | None = None, dotenv_path: str | Path | None = None) -> str:
    environment = os.environ if env is None else env
    value = environment.get(ENV_FRED_KEY, "").strip()
    path = DOTENV_PATH if env is None and dotenv_path is None else dotenv_path
    if not value and path is not None:
        value = read_dotenv(path, (ENV_FRED_KEY,)).get(ENV_FRED_KEY, "").strip()
    if not value:
        raise AlfredError(f"{ENV_FRED_KEY} is not set (environment or .env)")
    return value


def _get_json(endpoint: str, params: dict[str, str], key: str) -> dict:
    query = urllib.parse.urlencode({**params, "api_key": key, "file_type": "json"})
    request = urllib.request.Request(f"{API_ROOT}{endpoint}?{query}", headers={"User-Agent": USER_AGENT})
    label = f"{endpoint} {params.get('series_id', '')}".strip()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.load(exc).get("error_message", "")
        except Exception:
            pass
        detail = detail.replace(key, "<key>")
        raise AlfredError(f"{label}: HTTP {exc.code} {detail}".rstrip()) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AlfredError(f"{label}: {type(exc).__name__}") from None


def fetch_series_info(series_id: str, key: str, get=_get_json) -> dict:
    return get("series", {"series_id": series_id}, key)["seriess"][0]


def fetch_vintage_rows(series_id: str, key: str, get=_get_json) -> list[list[str]]:
    """Every revision row of a series: [date, realtime_start, realtime_end, value], all pages."""
    rows: list[list[str]] = []
    offset = 0
    while True:
        params = {
            "series_id": series_id,
            "realtime_start": EARLIEST,
            "realtime_end": LATEST,
            "output_type": "1",
            "limit": str(PAGE_LIMIT),
            "offset": str(offset),
            "sort_order": "asc",
        }
        page = get("series/observations", params, key)
        batch = page.get("observations", [])
        rows.extend([o["date"], o["realtime_start"], o["realtime_end"], o["value"]] for o in batch)
        offset += len(batch)
        if not batch or offset >= int(page.get("count", 0)):
            return rows


def write_rows(rows: Iterable[list[str]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)
    tmp.replace(out)


# --- reconstructing releases ------------------------------------------------------------------


@dataclass(frozen=True)
class Revision:
    period: date
    start: date  # first real-time day this value was published
    end: date  # last real-time day it stood (date.max while current)
    value: Optional[float]  # None where ALFRED shows "." (no value in that vintage)


def _day(text: str) -> date:
    return date.max if text == LATEST else date.fromisoformat(text)


def parse_rows(lines: Iterable[str]) -> list[Revision]:
    revisions = []
    for rec in csv.DictReader(lines):
        text = rec["value"].strip()
        value = None if text in ("", ".") else float(text)
        revisions.append(Revision(_day(rec["date"]), _day(rec["realtime_start"]), _day(rec["realtime_end"]), value))
    return revisions


def load_rows(path: str | Path) -> list[Revision]:
    with open(path, encoding="utf-8", newline="") as f:
        return parse_rows(f)


def _shift_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


@dataclass(frozen=True)
class Release:
    period: date
    released: date  # ALFRED date of first publication
    value: float  # the transformed indicator, as computed on the release date


class Vintages:
    """Point-in-time lookups over a series' revision rows."""

    def __init__(self, revisions: Iterable[Revision]):
        by_period: dict[date, list[Revision]] = defaultdict(list)
        for r in revisions:
            by_period[r.period].append(r)
        self._rows = {p: sorted(rs, key=lambda r: r.start) for p, rs in by_period.items()}
        self._starts = {p: [r.start for r in rs] for p, rs in self._rows.items()}
        self.periods = sorted(self._rows)
        self.step_months = self._step()

    def _step(self) -> int:
        if len(self.periods) < 2:
            return 1
        gaps = sorted(
            (b.year - a.year) * 12 + b.month - a.month for a, b in zip(self.periods, self.periods[1:])
        )
        return max(1, gaps[len(gaps) // 2])

    def first_published(self, period: date) -> Optional[date]:
        for r in self._rows.get(period, ()):
            if r.value is not None:
                return r.start
        return None

    def value(self, period: date, vintage: date) -> Optional[float]:
        rows = self._rows.get(period)
        if not rows:
            return None
        i = bisect_right(self._starts[period], vintage) - 1
        if i < 0 or rows[i].end < vintage:
            return None
        return rows[i].value

    def releases(self, transform: str = "level") -> list[Release]:
        if transform not in TRANSFORMS:
            raise ValueError(f"unknown transform {transform!r}; use one of {TRANSFORMS}")
        published = {p: self.first_published(p) for p in self.periods}
        dates = [d for d in published.values() if d is not None]
        if not dates:
            return []
        backfill = min(dates)
        lag = 12 if transform == "yoy" else self.step_months
        out = []
        for period in self.periods:
            released = published[period]
            if released is None or released == backfill:
                continue
            now = self.value(period, released)
            if transform == "level":
                out.append(Release(period, released, now))
                continue
            before = self.value(_shift_months(period, -lag), released)
            if before is None:
                continue
            if transform == "diff":
                out.append(Release(period, released, now - before))
            elif before != 0:
                out.append(Release(period, released, 100.0 * (now / before - 1.0)))
        return out


def known_from(released: date, clock: Optional[time] = None, currency: str = "USD") -> datetime:
    if clock is not None:
        return to_utc(currency, datetime.combine(released, clock))
    return to_utc("USD", datetime.combine(released + timedelta(days=1), time(0)))


def release_series(
    revisions: Iterable[Revision],
    transform: str = "level",
    clock: Optional[time] = None,
    currency: str = "USD",
) -> Series:
    """Each release's indicator as first seen, stamped with when a backtest may use it."""
    return Series(
        Observation(r.period, r.value, known_from(r.released, clock, currency))
        for r in Vintages(revisions).releases(transform)
    )
