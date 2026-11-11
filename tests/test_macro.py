"""Tests for carry.macro loaders and scripts/fetch_macro.py, on synthetic data (no network)."""

import io
import zipfile
from datetime import date, datetime, timezone

import pytest

from carry.macro import (
    CotReport,
    Observation,
    Series,
    cot_net_series,
    load_cot,
    parse_cot,
    parse_daily_candles,
    parse_fred,
    parse_policy_rates,
)
from scripts import fetch_macro


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def lines(text):
    return io.StringIO(text)


def test_series_point_in_time_lookups():
    s = Series(
        [
            Observation(date(2024, 1, 2), 2.0, utc(2024, 1, 3)),
            Observation(date(2024, 1, 1), 1.0, utc(2024, 1, 2)),
            Observation(date(2024, 1, 3), 3.0, utc(2024, 1, 4)),
        ]
    )
    assert s.asof(utc(2024, 1, 1, 23)) is None
    assert s.asof(utc(2024, 1, 3)).value == 2.0  # known exactly at known_from
    assert s.asof(utc(2024, 1, 3, 23, 59)).value == 2.0
    assert [o.value for o in s.last(utc(2024, 1, 9), 2)] == [2.0, 3.0]
    assert [o.value for o in s.last(utc(2024, 1, 2, 12), 5)] == [1.0]


def test_fred_skips_missing_and_is_known_next_day():
    s = parse_fred(lines("observation_date,VIXCLS\n2020-03-16,82.69\n2020-03-17,.\n2020-03-18,\n2020-03-19,72.0\n"))
    assert [(o.date, o.value) for o in s.observations] == [(date(2020, 3, 16), 82.69), (date(2020, 3, 19), 72.0)]
    assert s.observations[0].known_from == utc(2020, 3, 17)
    assert s.asof(utc(2020, 3, 16, 23)) is None


def test_policy_rates_map_areas_and_drop_nan():
    text = (
        "FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE\n"
        "D,XM,2024-06-12,4.0\nD,XM,2024-06-13,NaN\nD,XM,2024-06-14,3.75\n"
        "D,NZ,2024-06-12,5.5\nD,CN,2024-06-12,3.45\n"
    )
    rates = parse_policy_rates(lines(text))
    assert set(rates) == {"EUR", "NZD"}
    assert [o.value for o in rates["EUR"].observations] == [4.0, 3.75]
    assert rates["EUR"].asof(utc(2024, 6, 14, 12)).value == 4.0  # 3.75 effective 14th, used from the 15th
    assert rates["EUR"].asof(utc(2024, 6, 15)).value == 3.75


COT_HEADER = (
    '"Market and Exchange Names","As of Date in Form YYMMDD","As of Date in Form YYYY-MM-DD",'
    '"CFTC Contract Market Code","Noncommercial Positions-Long (All)","Noncommercial Positions-Short (All)"\n'
)


def cot_row(name, day, code, long, short):
    return f'"{name}",{day.strftime("%y%m%d")},{day.isoformat()},{code},{long},{short}\n'


def test_cot_parses_currencies_and_release_time():
    text = COT_HEADER + (
        cot_row("EURO FX - CHICAGO MERCANTILE EXCHANGE", date(2024, 1, 2), "099741", "  250000", " 100000")
        + cot_row("USD INDEX - ICE FUTURES U.S.", date(2024, 1, 2), "098662", "20000", "5000")
        + cot_row("CORN - CHICAGO BOARD OF TRADE", date(2024, 1, 2), "002602", "1", "2")
    )
    reports = parse_cot(lines(text))
    assert [(r.currency, r.net) for r in reports] == [("EUR", 150000), ("USD", 15000)]
    # Tuesday 2 Jan as-of -> Friday 5 Jan 15:30 New York (EST) = 20:30 UTC.
    assert reports[0].known_from == utc(2024, 1, 5, 20, 30)
    # Summer: Friday 15:30 EDT = 19:30 UTC.
    assert CotReport("EUR", date(2024, 7, 2), 1, 0).known_from == utc(2024, 7, 5, 19, 30)


def test_cot_series_dedupes_and_orders():
    reports = [
        CotReport("JPY", date(2024, 1, 9), 10, 30),
        CotReport("JPY", date(2024, 1, 2), 10, 20),
        CotReport("JPY", date(2024, 1, 9), 10, 30),
    ]
    s = cot_net_series(reports)["JPY"]
    assert [(o.date, o.value) for o in s.observations] == [(date(2024, 1, 2), -10.0), (date(2024, 1, 9), -20.0)]
    assert s.asof(utc(2024, 1, 12, 12)).date == date(2024, 1, 2)  # 9 Jan report not out until 20:30


def test_load_cot_reads_zips_without_extracting(tmp_path):
    for year, day in ((2023, date(2023, 12, 26)), (2024, date(2024, 1, 2))):
        with zipfile.ZipFile(tmp_path / f"deacot{year}.zip", "w") as z:
            z.writestr("annual.txt", COT_HEADER + cot_row("NZ DOLLAR - CHICAGO MERCANTILE EXCHANGE", day, "112741", 5, 7))
    s = load_cot(tmp_path)["NZD"]
    assert [o.date for o in s.observations] == [date(2023, 12, 26), date(2024, 1, 2)]
    assert not list(tmp_path.glob("*.txt"))


def test_daily_candles_known_at_close():
    s = parse_daily_candles(
        lines("time,open,high,low,close,spread_pips\n2024-01-07T22:00:00Z,4700,4720,4690,4710,0.5\n")
    )
    (o,) = s.observations
    assert (o.date, o.value, o.known_from) == (date(2024, 1, 8), 4710.0, utc(2024, 1, 8, 22))


def test_fetch_macro_jobs_and_skips(tmp_path):
    fetched = []

    def fake_fetch(url, out):
        fetched.append(out.name)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return 1

    (tmp_path / "cot").mkdir()
    (tmp_path / "cot" / "deacot2024.zip").write_bytes(b"old")
    fetch_macro.main(["--out-dir", str(tmp_path), "--start-year", "2024"], fetch=fake_fetch, today=date(2025, 3, 1))
    assert fetched == ["fred_VIXCLS.csv", "fred_T10Y2Y.csv", "fred_DGS2.csv", "bis_cbpol.csv", "deacot2025.zip"]


def test_fetch_macro_reports_failures(tmp_path):
    def failing(url, out):
        raise OSError("offline")

    with pytest.raises(SystemExit):
        fetch_macro.main(["--out-dir", str(tmp_path), "--start-year", "2025"], fetch=failing, today=date(2025, 3, 1))
