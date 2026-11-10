"""Tests for carry.calendar, on small synthetic Forex Factory-style CSVs (no real data)."""

from datetime import datetime, timedelta, timezone

import pytest

from carry.calendar import categorize, parse_calendar, parse_effect, parse_value

HEADER = "DateTime,Currency,Impact,Event,Actual,Forecast,Previous,Detail\n"
GREATER = "Usual Effect: 'Actual' greater than 'Forecast' is good for currency;"
LESS = "Usual Effect: 'Actual' less than 'Forecast' is good for currency;"
HIGH = "High Impact Expected"


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def csv_lines(*rows):
    body = "".join(",".join(f'"{v}"' for v in row) + "\n" for row in rows)
    return (HEADER + body).splitlines(keepends=True)


@pytest.mark.parametrize(
    "text, value",
    [
        ("0.4%", 0.4),
        ("-0.2%", -0.2),
        ("143K", 143_000),
        ("-1.5B", -1.5e9),
        ("2.1T", 2.1e12),
        ("12k", 12_000),
        ("<0.10%", 0.10),
        ("3.94|1.8", 3.94),
        ("0.3%%", 0.3),
        ("7-2-0", None),
        ("Pass", None),
        ("", None),
    ],
)
def test_parse_value(text, value):
    assert parse_value(text) == (pytest.approx(value) if value is not None else None)


def test_parse_effect():
    assert parse_effect(f"Source: X | {GREATER} | Frequency: monthly") == 1
    assert parse_effect(LESS) == -1
    assert parse_effect("Usual Effect: More hawkish than expected is good for currency;") == 1
    assert parse_effect("Usual Effect: No consistent effect - there are both risk and growth implications;") == 0
    assert parse_effect("no effect line") == 0


@pytest.mark.parametrize(
    "event, category",
    [
        ("Core CPI m/m", "inflation"),
        ("Non-Farm Employment Change", "employment"),
        ("Unemployment Claims", "employment"),
        ("Advance GDP q/q", "gdp"),
        ("Trade Balance", "trade"),
        ("German ZEW Economic Sentiment", "sentiment"),
        ("ISM Manufacturing PMI", "business"),
        ("Advance GDP Price Index q/q", "inflation"),
        ("Average Cash Earnings y/y", "employment"),
        ("Average Earnings Index 3m/y", "employment"),
        ("SECO Consumer Climate", "sentiment"),
        ("UBS Economic Expectations", "sentiment"),
        ("Economy Watchers Sentiment", "sentiment"),
        ("Philly Fed Manufacturing Index", "business"),
        ("BusinessNZ Manufacturing Index", "business"),
        ("Tourism Arrivals", None),
        ("FOMC Statement", None),
    ],
)
def test_categorize(event, category):
    assert categorize(event) == category


def test_exact_times_convert_to_utc_and_values_parse():
    (r,) = parse_calendar(csv_lines(("2024-12-11T17:00:00+03:30", "USD", HIGH, "Core CPI m/m", "0.3%", "0.3%", "0.3%", GREATER)))
    assert r.time == utc(2024, 12, 11, 13, 30) and r.time_source == "exact"
    assert (r.actual, r.forecast, r.previous, r.effect, r.category, r.impact) == (0.3, 0.3, 0.3, 1, "inflation", "high")
    assert r.surprise() == 0 and r.window() == (r.time, r.time) and r.known_from == r.time


def test_surprise_respects_direction():
    rows = csv_lines(
        ("2025-02-07T17:00:00+03:30", "CAD", HIGH, "Employment Change", "76.0K", "25.5K", "", GREATER),
        ("2025-02-07T17:00:00+03:30", "CAD", HIGH, "Unemployment Rate", "6.6%", "6.8%", "", LESS),
        ("2025-02-07T17:00:00+03:30", "CAD", HIGH, "Wages", "1.0%", "", "", GREATER),
    )
    jobs, unemployment, wages = parse_calendar(rows)  # sorted by time, then event name
    assert jobs.surprise() == 1 and unemployment.surprise() == 1  # lower unemployment is a beat
    assert wages.surprise() is None  # no forecast


def test_untimed_row_gets_usual_local_time_across_daylight_saving():
    rows = csv_lines(
        # NFP with real times: 8:30 New York in summer (12:30 UTC) and winter (13:30 UTC).
        ("2024-07-05T16:00:00+03:30", "USD", HIGH, "Non-Farm Employment Change", "206K", "191K", "", GREATER),
        ("2024-12-06T17:00:00+03:30", "USD", HIGH, "Non-Farm Employment Change", "227K", "202K", "", GREATER),
        # Date only (00:00 Tehran) in winter: must land on 13:30 UTC, not the summer 12:30.
        ("2025-02-07T00:00:00+03:30", "USD", HIGH, "Non-Farm Employment Change", "143K", "169K", "", GREATER),
    )
    *_, feb = parse_calendar(rows)
    assert feb.time == utc(2025, 2, 7, 13, 30) and feb.time_source == "imputed"


def test_sibling_supplies_time_for_event_that_never_has_one():
    rows = csv_lines(
        ("2024-12-06T17:00:00+03:30", "USD", HIGH, "Non-Farm Employment Change", "227K", "202K", "", GREATER),
        ("2025-02-07T00:00:00+03:30", "USD", HIGH, "Unemployment Rate", "4.0%", "4.1%", "", LESS),
    )
    _, unemployment = parse_calendar(rows)
    assert unemployment.time == utc(2025, 2, 7, 13, 30) and unemployment.time_source == "imputed"


def test_untimed_asian_release_stays_inside_its_tehran_day():
    # AUD jobs at 11:30 Sydney (00:30 UTC in winter = 04:00 Tehran the same date).
    rows = csv_lines(
        ("2024-06-13T05:00:00+04:30", "AUD", HIGH, "Employment Change", "39.7K", "30.0K", "", GREATER),
        ("2024-07-18T00:00:00+03:30", "AUD", HIGH, "Employment Change", "50.2K", "20.0K", "", GREATER),
    )
    _, july = parse_calendar(rows)
    assert july.time == utc(2024, 7, 18, 1, 30) and july.time_source == "imputed"


def test_unplaceable_and_all_day_rows_are_unknown_and_cover_the_day():
    rows = csv_lines(
        ("2025-02-07T00:00:00+03:30", "GBP", HIGH, "Mystery Release", "1.0", "0.5", "", GREATER),
        ("2025-02-07T23:59:59+03:30", "EUR", "Non-Economic", "Bank Holiday", "", "", "", ""),
    )
    mystery, holiday = parse_calendar(rows)
    day_start = utc(2025, 2, 6, 20, 30)  # 00:00 Tehran
    for r in (mystery, holiday):
        assert r.time_source == "unknown"
        assert r.window() == (day_start, day_start + timedelta(days=1))
        assert r.known_from == day_start + timedelta(days=1)


def test_2023_summer_tehran_offset_is_corrected():
    # Iran had no daylight saving in 2023; the file still says +04:30. True NFP time: 12:30 UTC.
    (r,) = parse_calendar(csv_lines(("2023-07-07T16:00:00+04:30", "USD", HIGH, "Non-Farm Employment Change", "209K", "225K", "", GREATER)))
    assert r.time == utc(2023, 7, 7, 12, 30)


def test_2022_summer_offset_is_kept():
    (r,) = parse_calendar(csv_lines(("2022-07-08T17:00:00+04:30", "USD", HIGH, "Non-Farm Employment Change", "372K", "268K", "", GREATER)))
    assert r.time == utc(2022, 7, 8, 12, 30)


def test_filters_currencies_and_rejects_unsupported():
    rows = csv_lines(
        ("2025-02-07T17:00:00+03:30", "CNY", HIGH, "Trade Balance", "1B", "", "", GREATER),
        ("2025-02-07T17:00:00+03:30", "USD", HIGH, "Trade Balance", "1B", "", "", GREATER),
    )
    assert [r.currency for r in parse_calendar(rows)] == ["USD"]
    with pytest.raises(ValueError):
        parse_calendar(rows, currencies=["CNY"])


def test_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        parse_calendar(csv_lines(("2025-02-07T17:00:00", "USD", HIGH, "X", "", "", "", "")))
