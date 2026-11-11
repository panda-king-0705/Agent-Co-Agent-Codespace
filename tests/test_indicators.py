"""Tests for carry.indicators and scripts/fetch_indicators.py, on synthetic files (no network)."""

from datetime import date, datetime, timezone

import pytest

from carry import indicators
from carry.alfred import fetch_latest_csv
from carry.indicators import (
    CATALOG,
    CATEGORIES,
    Indicator,
    category_signal,
    load_indicator,
    parse_values,
    revised_known_from,
    revised_series,
    signal_at,
    to_quarterly,
    transformed,
)
from carry.macro import Observation, Series
from scripts import fetch_indicators


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_catalog_covers_every_cell_once():
    cells = {(i.currency, i.category) for i in CATALOG}
    assert len(cells) == len(CATALOG) == 48
    assert {c for _, c in cells} == set(CATEGORIES)
    assert all(i.source == "alfred" for i in CATALOG if i.currency == "USD")
    assert all(i.direction == -1 for i in CATALOG if i.category == "employment" and i.currency != "USD")


def test_parse_each_source():
    assert parse_values("fred", "observation_date,X\n2024-01-01,1.5\n2024-02-01,.\n") == {date(2024, 1, 1): 1.5}
    assert parse_values("bis", "FREQ,REF_AREA,UNIT_MEASURE,TIME_PERIOD,OBS_VALUE\nM,JP,628,2024-03,107.2\n") == {
        date(2024, 3, 1): 107.2
    }
    eurostat = "DATAFLOW,LAST UPDATE,freq,geo,TIME_PERIOD,OBS_VALUE,OBS_FLAG\nESTAT:X(1.0),1/1/26,M,EA21,2024-05,6.4,\n"
    assert parse_values("eurostat", eurostat) == {date(2024, 5, 1): 6.4}
    boc = '﻿"TERMS AND CONDITIONS"\n"x"\n"SERIES"\n"id","label"\n"PC1","BOS"\n\n"OBSERVATIONS"\n"date","PC1"\n"2026Q3","-0.33"\n'
    assert parse_values("boc", boc) == {date(2026, 7, 1): -0.33}


def test_quarterly_collapse_and_transforms():
    monthly = {date(2024, m, 1): v for m, v in [(1, 10), (2, 10), (3, 10), (4, 11), (5, 11), (6, 11)]}
    q = to_quarterly(monthly)
    assert q == {date(2024, 1, 1): 10, date(2024, 4, 1): 11}
    assert transformed(q, "pct", 3) == {date(2024, 4, 1): pytest.approx(10.0)}
    assert transformed(q, "diff", 3) == {date(2024, 4, 1): 1}
    yearly = {date(2023, 4, 1): 100.0, date(2024, 4, 1): 103.0}
    assert transformed(yearly, "yoy", 1) == {date(2024, 4, 1): pytest.approx(3.0)}


def test_revised_known_from_uses_lag_and_home_midnight():
    eur = Indicator("EUR", "inflation", "bis", "XM", "yoy", lag_days=20)
    # Sep 2026 ends 30 Sep; +20 days -> 20 Oct; usable from 21 Oct 00:00 CEST = 20 Oct 22:00 UTC.
    assert revised_known_from(eur, date(2026, 9, 1)) == utc(2026, 10, 20, 22)
    boc = Indicator("CAD", "sentiment", "boc", "BOS_CHART_11", freq="Q", lag_days=-70)
    # Q3 2026 ends 30 Sep; -70 days -> 22 Jul; usable from 23 Jul 00:00 EDT = 04:00 UTC.
    assert revised_known_from(boc, date(2026, 7, 1)) == utc(2026, 7, 23, 4)


def test_revised_series_end_to_end():
    ind = Indicator("GBP", "employment", "fred", "U", direction=-1, lag_days=50)
    s = revised_series(ind, "observation_date,U\n2026-05-01,4.8\n2026-06-01,4.9\n")
    assert [o.value for o in s.observations] == [4.8, 4.9]
    assert s.asof(utc(2026, 8, 19, 22, 59)).value == 4.8
    assert s.asof(utc(2026, 8, 19, 23)).value == 4.9  # 31 Jul + 50d = 19 Aug, midnight BST


@pytest.mark.parametrize(
    "readings, direction, expected",
    [
        ([1, 2, 3], 1, 1),
        ([3, 2, 1], 1, -1),
        ([1, 3, 2], 1, 0),  # trend up, but missed the previous release
        ([2, 1, 3], 1, 1),
        ([3, 1, 2], 1, 0),  # beat previous, trend still down
        ([5.0, 4.9, 4.8], -1, 1),  # falling unemployment is good
        ([1, 2], 1, 0),
        ([2, 2, 2], 1, 0),
    ],
)
def test_category_signal(readings, direction, expected):
    assert category_signal(readings, direction) == expected


def test_signal_at_uses_only_known_releases():
    s = Series(Observation(date(2026, m, 1), v, utc(2026, m + 1, 15)) for m, v in [(1, 1.0), (2, 2.0), (3, 3.0), (4, 0.0)])
    assert signal_at(s, utc(2026, 5, 1)) == 1  # April's 0.0 not out until 15 May
    assert signal_at(s, utc(2026, 5, 15)) == -1


def test_load_indicator_reads_alfred_and_revised(tmp_path):
    (tmp_path / "alfred").mkdir()
    (tmp_path / "alfred" / "UNRATE.csv").write_text(
        "date,realtime_start,realtime_end,value\n2026-01-01,2026-02-06,9999-12-31,4.0\n2026-02-01,2026-03-06,9999-12-31,4.1\n"
    )
    us = Indicator("USD", "employment", "alfred", "UNRATE", direction=-1, clock=indicators.time(8, 30))
    assert [o.value for o in load_indicator(us, tmp_path).observations] == [4.1]  # Jan is the backfill
    (tmp_path / "indicators").mkdir()
    (tmp_path / "indicators" / "JPY_trade.csv").write_text("observation_date,T\n2026-01-01,-5\n")
    jp = Indicator("JPY", "trade", "fred", "T", lag_days=25)
    assert load_indicator(jp, tmp_path).observations[0].value == -5


def test_fetch_latest_csv_formats_fredgraph():
    def fake_get(endpoint, params, key):
        return {"observations": [{"date": "2026-01-01", "value": "1.5"}, {"date": "2026-02-01", "value": "."}]}

    assert fetch_latest_csv("X", "k", get=fake_get) == "observation_date,X\n2026-01-01,1.5\n2026-02-01,.\n"


def test_fetch_indicators_routes_sources(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FRED_API_KEY", "k")
    small = (
        Indicator("USD", "employment", "alfred", "UNRATE", direction=-1),
        Indicator("JPY", "trade", "fred", "T", lag_days=25),
        Indicator("EUR", "inflation", "bis", "XM", "yoy", lag_days=20),
    )
    monkeypatch.setattr(fetch_indicators, "CATALOG", small)
    seen = []

    def fake_fetch(url, out):
        seen.append(url)
        raise OSError("offline")

    def fake_rows(series_id, key):
        return [["2026-01-01", "2026-02-06", "9999-12-31", "4.0"], ["2026-02-01", "2026-03-06", "9999-12-31", "4.1"]]

    def fake_latest(series_id, key):
        return "observation_date,T\n2026-01-01,-5\n"

    with pytest.raises(SystemExit, match="EUR_inflation"):
        fetch_indicators.main(["--data-dir", str(tmp_path)], fetch=fake_fetch, fetch_rows=fake_rows, fetch_latest=fake_latest)
    out = capsys.readouterr().out
    assert seen == [small[2].url] and "stats.bis.org" in seen[0]
    assert "USD_employment   alfred" in out and "JPY_trade        fred" in out and "EUR_inflation    missing" in out
