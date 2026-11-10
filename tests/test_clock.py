"""Tests for carry.clock: home-market local time with daylight-saving rules."""

from datetime import date, datetime, timedelta, timezone

import pytest

from carry.clock import to_local, to_utc, utc_offset


@pytest.mark.parametrize(
    "currency, day, hours",
    [
        ("USD", date(2025, 3, 8), -5),  # day before the 2nd Sunday of March
        ("USD", date(2025, 3, 9), -4),
        ("USD", date(2025, 11, 1), -4),
        ("USD", date(2025, 11, 2), -5),  # 1st Sunday of November
        ("USD", date(2006, 4, 1), -5),  # pre-2007 rule: 1st Sunday of April
        ("USD", date(2006, 4, 2), -4),
        ("CAD", date(2024, 7, 1), -4),
        ("GBP", date(2025, 3, 29), 0),
        ("GBP", date(2025, 3, 30), 1),  # last Sunday of March
        ("EUR", date(2025, 10, 26), 1),  # last Sunday of October
        ("CHF", date(2025, 7, 1), 2),
        ("JPY", date(2025, 7, 1), 9),
        ("AUD", date(2025, 1, 15), 11),
        ("AUD", date(2025, 4, 6), 10),  # 1st Sunday of April
        ("AUD", date(2025, 10, 5), 11),  # 1st Sunday of October
        ("AUD", date(2007, 3, 25), 10),  # pre-2008: last Sunday of March
        ("NZD", date(2025, 9, 28), 13),  # last Sunday of September
        ("NZD", date(2025, 4, 5), 13),
        ("NZD", date(2025, 4, 6), 12),
    ],
)
def test_utc_offsets(currency, day, hours):
    assert utc_offset(currency, day) == timedelta(hours=hours)


def test_round_trip_and_us_payrolls_time():
    nfp_winter = datetime(2025, 2, 7, 13, 30, tzinfo=timezone.utc)
    nfp_summer = datetime(2024, 7, 5, 12, 30, tzinfo=timezone.utc)
    assert to_local("USD", nfp_winter) == datetime(2025, 2, 7, 8, 30)
    assert to_local("USD", nfp_summer) == datetime(2024, 7, 5, 8, 30)
    assert to_utc("USD", datetime(2025, 2, 7, 8, 30)) == nfp_winter


def test_local_date_can_differ_from_utc_date():
    # 21:45 UTC on 1 May is 09:45 on 2 May in Wellington (standard time, +12).
    assert to_local("NZD", datetime(2025, 5, 1, 21, 45, tzinfo=timezone.utc)) == datetime(2025, 5, 2, 9, 45)
