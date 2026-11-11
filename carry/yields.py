"""Daily 2-year government bond yields for the 8 economies, for the yield-spread gate.

Sources (all official, free, no key):
- USD: FRED DGS2 (US Treasury constant maturity).
- EUR: Bundesbank, German federal securities, 2-year residual maturity (Svensson curve).
- GBP: Bank of England nominal spot curve, 2 years (zip of xlsx workbooks).
- JPY: Ministry of Finance JGB curve: the full history file plus the current month's file.
- CHF: SNB, Confederation bond yields, 2 years.
- CAD: Bank of Canada, 2-year benchmark bond yield.
- AUD: RBA table F2 via DBnomics (the RBA site refuses scripts). DBnomics only starts in
  2013, so AUD has no yield before then.
- NZD: RBNZ table B2. The RBNZ site refuses scripts, so the file is downloaded by hand.

A yield is a market price, so the value for day d is used from d+1 00:00 UTC, after every
one of these markets has closed.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from carry.macro import Observation, Series
from carry.xlsx import excel_date, read_sheet

# currency -> [(file name, url)]; files land in data/macro/yields/
SOURCES: dict[str, list[tuple[str, str]]] = {
    "USD": [("USD.csv", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2")],
    "EUR": [
        (
            "EUR.csv",
            "https://api.statistiken.bundesbank.de/rest/download/BBSIS/"
            "D.I.ZST.ZI.EUR.S1311.B.A604.R02XX.R.A.A._Z._Z.A?format=csv&lang=en",
        )
    ],
    "GBP": [("GBP.zip", "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/glcnominalddata.zip")],
    "JPY": [
        ("JPY_all.csv", "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv"),
        ("JPY_month.csv", "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"),
    ],
    "CHF": [("CHF.csv", "https://data.snb.ch/api/cube/rendeiduebd/data/csv/en?dimSel=D0(CHF),D1(2J)&fromDate=2005-01-01")],
    "CAD": [("CAD.csv", "https://www.bankofcanada.ca/valet/observations/BD.CDN.2YR.DQ.YLD/csv?start_date=2005-01-01")],
    "AUD": [("AUD.csv", "https://api.db.nomics.world/v22/series/RBA/F2/FCMYGBAG2D?observations=1&format=csv")],
}

# Files only served to browsers: save them by hand into data/macro/yields/manual/.
MANUAL = {
    # The current file starts in 2018; the RBNZ's older B2 daily-close history file can be
    # saved alongside it (any hb2-daily*.xlsx name) to extend NZD back before 2018.
    "NZD": ("hb2-daily-close.xlsx", "https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates (B2 daily close)"),
}

BOE_FIRST_YEAR = 2005  # workbooks ending before this are skipped


def _number(text: str) -> Optional[float]:
    text = text.strip().strip('"')
    if text in ("", ".", "-", "NA", "NaN"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _iso(text: str) -> Optional[date]:
    try:
        return date.fromisoformat(text.strip().strip('"'))
    except ValueError:
        return None


def parse_dated_csv(text: str, delimiter: str = ",", value_col: int = 1) -> dict[date, float]:
    """Rows whose first column is an ISO date and value_col a number; everything else skipped.

    Covers FRED, Bundesbank (metadata rows on top), Bank of Canada Valet (metadata block),
    SNB (semicolons, value in the last column) and DBnomics (NA for gaps).
    """
    values = {}
    for row in csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=delimiter):
        if len(row) <= max(1, value_col if value_col >= 0 else 1):
            continue
        day = _iso(row[0])
        value = _number(row[value_col]) if day else None
        if day and value is not None:
            values[day] = value
    return values


def parse_snb(text: str) -> dict[date, float]:
    return parse_dated_csv(text, delimiter=";", value_col=-1)


_MOF_DATE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")


def parse_mof(text: str) -> dict[date, float]:
    """MoF JGB curve CSV: Date,1Y,2Y,... with dates like 2026/11/2 and '-' for missing."""
    values = {}
    column = 2
    for row in csv.reader(io.StringIO(text)):
        if row and row[0].strip() == "Date" and "2Y" in row:
            column = row.index("2Y")
            continue
        match = _MOF_DATE.match(row[0].strip()) if row else None
        if match and len(row) > column:
            value = _number(row[column])
            if value is not None:
                values[date(*map(int, match.groups()))] = value
    return values


def parse_boe_zip(data: bytes, first_year: int = BOE_FIRST_YEAR) -> dict[date, float]:
    """BoE glcnominalddata.zip: sheet '4. spot curve' of each workbook, the 2.0-year column."""
    values = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in sorted(archive.namelist()):
            years = [int(y) for y in re.findall(r"(\d{4})", name)]
            if not name.endswith(".xlsx") or ("present" not in name and years and max(years) < first_year):
                continue
            values.update(parse_boe_sheet(read_sheet(archive.read(name), "4. spot curve")))
    return values


def parse_boe_sheet(rows: list[list]) -> dict[date, float]:
    header = next(r for r in rows if r and r[0] == "years:")
    column = header.index(2.0)
    values = {}
    for row in rows:
        if row and isinstance(row[0], (int, float)) and len(row) > column and isinstance(row[column], (int, float)):
            values[excel_date(row[0])] = row[column]
    return values


def parse_rbnz_sheet(rows: list[list], series_prefix: str = "INM.DG102") -> dict[date, float]:
    """RBNZ B2 workbook: a 'Series Id' header row naming each column; dates as Excel serials.

    INM.DG102.* is the 2-year secondary-market government bond closing yield
    (INM.DG102.NZZCF in the 2018-onward file).
    """
    header = next(r for r in rows if r and isinstance(r[0], str) and r[0].strip().lower() == "series id")
    column = next(i for i, sid in enumerate(header) if isinstance(sid, str) and sid.strip().startswith(series_prefix + "."))
    values = {}
    for row in rows:
        if row and isinstance(row[0], (int, float)) and len(row) > column and isinstance(row[column], (int, float)):
            values[excel_date(row[0])] = row[column]
    return values


def to_series(values: dict[date, float]) -> Series:
    return Series(
        Observation(day, value, datetime.combine(day + timedelta(days=1), time(0), tzinfo=timezone.utc))
        for day, value in values.items()
    )


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig", errors="replace")


def load_currency(currency: str, directory: str | Path) -> dict[date, float]:
    """Raw daily yields for one currency from the files in `directory` (missing files skipped)."""
    folder = Path(directory)
    values: dict[date, float] = {}
    if currency == "GBP":
        path = folder / "GBP.zip"
        if path.exists():
            values.update(parse_boe_zip(path.read_bytes()))
    elif currency == "JPY":
        for name in ("JPY_all.csv", "JPY_month.csv"):  # the month file overrides the history
            if (folder / name).exists():
                values.update(parse_mof(_read_text(folder / name)))
    elif currency == "CHF":
        if (folder / "CHF.csv").exists():
            values.update(parse_snb(_read_text(folder / "CHF.csv")))
    elif currency == "NZD":
        # Every RBNZ B2 workbook present (the 2018-onward file plus any older history file);
        # the current file sorts last and wins where they overlap.
        for path in sorted((folder / "manual").glob("hb2-daily*.xlsx"), key=lambda p: (p.name != MANUAL["NZD"][0], p.name), reverse=True):
            values.update(parse_rbnz_sheet(read_sheet(path.read_bytes(), "Data")))
    else:
        path = folder / f"{currency}.csv"
        if path.exists():
            values.update(parse_dated_csv(_read_text(path)))
    return values


def load_yields(directory: str | Path, currencies: Iterable[str] = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")) -> dict[str, Series]:
    return {c: to_series(load_currency(c, directory)) for c in currencies}


def spread_at(base: Series, quote: Series, when: datetime, max_age_days: int = 5) -> Optional[float]:
    """Base minus quote 2-year yield known at `when`, or None if either is missing or stale."""
    a, b = base.asof(when), quote.asof(when)
    if a is None or b is None:
        return None
    oldest = min(a.known_from, b.known_from)
    if when - oldest > timedelta(days=max_age_days):
        return None
    return a.value - b.value
