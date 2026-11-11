"""Tests for carry.alfred and scripts/fetch_alfred.py, on synthetic vintages (no network)."""

import io
import json
import urllib.error
from datetime import date, datetime, time, timezone

import pytest

from carry import alfred
from carry.alfred import AlfredError, Vintages, api_key, fetch_vintage_rows, known_from, parse_rows, release_series
from scripts import fetch_alfred

SECRET = "abcdef0123456789abcdef0123456789"


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def rows(text):
    return parse_rows(io.StringIO("date,realtime_start,realtime_end,value\n" + text))


# Backfill on 2020-01-15 (Dec, Jan). Feb first published 2020-03-10, when Jan was revised to 100.5.
# Mar published 2020-04-09. Apr has a "." row before its first real value on 2020-05-12.
CPI = rows(
    "2019-12-01,2020-01-15,9999-12-31,99.0\n"
    "2020-01-01,2020-01-15,2020-03-09,100.0\n"
    "2020-01-01,2020-03-10,9999-12-31,100.5\n"
    "2020-02-01,2020-03-10,2020-04-08,101.0\n"
    "2020-02-01,2020-04-09,9999-12-31,101.2\n"
    "2020-03-01,2020-04-09,9999-12-31,102.0\n"
    "2020-04-01,2020-04-09,2020-05-11,.\n"
    "2020-04-01,2020-05-12,9999-12-31,103.0\n"
)


def test_releases_skip_backfill_and_use_vintage_of_the_day():
    v = Vintages(CPI)
    assert v.step_months == 1
    assert [(r.period, r.released, r.value) for r in v.releases("level")] == [
        (date(2020, 2, 1), date(2020, 3, 10), 101.0),
        (date(2020, 3, 1), date(2020, 4, 9), 102.0),
        (date(2020, 4, 1), date(2020, 5, 12), 103.0),
    ]
    pct = v.releases("pct")
    assert pct[0].value == pytest.approx(100 * (101.0 / 100.5 - 1))  # revised Jan, as seen on 10 Mar
    assert pct[1].value == pytest.approx(100 * (102.0 / 101.2 - 1))
    assert [r.value for r in v.releases("diff")] == pytest.approx([0.5, 0.8, 1.0])


def test_reloaded_old_periods_are_not_releases():
    reloaded = CPI + rows(
        "2018-06-01,2020-06-01,9999-12-31,95.0\n"  # old period first valued after Apr was out
        "2020-05-01,2020-06-01,9999-12-31,104.0\n"  # the genuine release that day
    )
    assert [r.period for r in Vintages(reloaded).releases("level")] == [
        date(2020, 2, 1), date(2020, 3, 1), date(2020, 4, 1), date(2020, 5, 1)
    ]


def test_value_lookup_respects_realtime_window():
    v = Vintages(CPI)
    assert v.value(date(2020, 1, 1), date(2020, 3, 9)) == 100.0
    assert v.value(date(2020, 1, 1), date(2020, 3, 10)) == 100.5
    assert v.value(date(2020, 2, 1), date(2020, 3, 1)) is None


def test_yoy_and_quarterly_step():
    quarterly = rows(
        "2019-01-01,2019-05-01,9999-12-31,10.0\n"
        "2019-04-01,2019-05-01,9999-12-31,11.0\n"
        "2019-07-01,2019-10-30,9999-12-31,12.0\n"
        "2019-10-01,2020-01-30,9999-12-31,13.0\n"
        "2020-01-01,2020-04-29,9999-12-31,10.5\n"
    )
    v = Vintages(quarterly)
    assert v.step_months == 3
    assert [r.value for r in v.releases("pct")] == pytest.approx([100 * (12 / 11 - 1), 100 * (13 / 12 - 1), 100 * (10.5 / 13 - 1)])
    assert [r.value for r in v.releases("yoy")] == pytest.approx([5.0])
    with pytest.raises(ValueError):
        v.releases("log")


def test_known_from_default_and_clock():
    # Next midnight New York: 11 Mar 2020 00:00 EDT = 04:00 UTC.
    assert known_from(date(2020, 3, 10)) == utc(2020, 3, 11, 4)
    assert known_from(date(2020, 3, 10), time(8, 30), "USD") == utc(2020, 3, 10, 12, 30)
    s = release_series(CPI, "pct", time(8, 30))
    assert s.asof(utc(2020, 3, 10, 12, 29)) is None
    assert s.asof(utc(2020, 3, 10, 12, 30)).date == date(2020, 2, 1)


def test_api_key_sources(tmp_path):
    assert api_key({"FRED_API_KEY": " k "}) == "k"
    env_file = tmp_path / ".env"
    env_file.write_text(f"OANDA_API_TOKEN=x\nFRED_API_KEY={SECRET}\n", encoding="utf-8")
    assert api_key({}, env_file) == SECRET
    with pytest.raises(AlfredError, match="FRED_API_KEY") as err:
        api_key({}, tmp_path / "missing")
    assert SECRET not in str(err.value)


def test_fetch_pages_until_count():
    calls = []

    def fake_get(endpoint, params, key):
        calls.append(int(params["offset"]))
        start = int(params["offset"])
        batch = [{"date": f"2020-0{i + 1}-01", "realtime_start": "2020-06-01", "realtime_end": "9999-12-31", "value": str(i)}
                 for i in range(start, min(start + 2, 3))]
        return {"count": 3, "observations": batch}

    assert len(fetch_vintage_rows("X", "k", get=fake_get)) == 3
    assert calls == [0, 2]


def test_http_errors_never_show_the_key(monkeypatch):
    def failing(request, timeout):
        body = io.BytesIO(json.dumps({"error_message": f"Bad api_key {SECRET}"}).encode())
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, body)

    monkeypatch.setattr(alfred.urllib.request, "urlopen", failing)
    with pytest.raises(AlfredError) as err:
        alfred.fetch_series_info("CPIAUCSL", SECRET)
    assert SECRET not in str(err.value) and "HTTP 400" in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_fetch_alfred_writes_and_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FRED_API_KEY", "k")

    def fake_rows(series_id, key):
        if series_id == "BAD":
            raise AlfredError("series BAD: HTTP 400")
        return [[str(r.period), str(r.start), "9999-12-31" if r.end == date.max else str(r.end),
                 "." if r.value is None else str(r.value)] for r in CPI]

    def fake_info(series_id, key):
        return {"title": "CPI", "frequency_short": "M", "observation_end": "2020-04-01"}

    fetch_alfred.main(["CPIAUCSL", "BAD", "--out-dir", str(tmp_path)], fetch_rows=fake_rows, fetch_info=fake_info)
    out = capsys.readouterr().out
    assert (tmp_path / "CPIAUCSL.csv").exists() and not (tmp_path / "BAD.csv").exists()
    assert "releases    3" in out and "BAD                  missing" in out and "1 series failed: BAD" in out
