"""Home-market local time for the 8 currencies, with daylight-saving rules from 2000 on.

Standard library only (Windows Python has no time-zone database without the tzdata package).
Daylight saving is decided per calendar date, ignoring the hour of the switch: economic
releases are not scheduled in the small hours of a Sunday, which is when clocks change.

Home clocks: USD and CAD New York/Toronto, EUR and CHF Central Europe, GBP London,
JPY Tokyo (no daylight saving), AUD Sydney, NZD Wellington.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _last_sunday(year: int, month: int) -> date:
    last = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def _north_america(day: date) -> bool:
    y = day.year
    if y >= 2007:
        return _nth_sunday(y, 3, 2) <= day < _nth_sunday(y, 11, 1)
    return _nth_sunday(y, 4, 1) <= day < _last_sunday(y, 10)


def _europe(day: date) -> bool:
    return _last_sunday(day.year, 3) <= day < _last_sunday(day.year, 10)


def _sydney(day: date) -> bool:
    y = day.year
    if y == 2006:
        end = date(2006, 4, 2)  # extended for the Commonwealth Games
    else:
        end = _nth_sunday(y, 4, 1) if y >= 2008 else _last_sunday(y, 3)
    start = _nth_sunday(y, 10, 1) if y >= 2008 else _last_sunday(y, 10)
    return day < end or day >= start


def _wellington(day: date) -> bool:
    y = day.year
    end = _nth_sunday(y, 4, 1) if y >= 2008 else _nth_sunday(y, 3, 3)
    start = _last_sunday(y, 9) if y >= 2007 else _nth_sunday(y, 10, 1)
    return day < end or day >= start


# currency -> (standard UTC offset in hours, daylight-saving rule or None)
_ZONES = {
    "USD": (-5, _north_america),
    "CAD": (-5, _north_america),
    "EUR": (1, _europe),
    "CHF": (1, _europe),
    "GBP": (0, _europe),
    "JPY": (9, None),
    "AUD": (10, _sydney),
    "NZD": (12, _wellington),
}


def utc_offset(currency: str, day: date) -> timedelta:
    """Home-market UTC offset of `currency` on local calendar date `day`."""
    standard, rule = _ZONES[currency]
    return timedelta(hours=standard + (1 if rule and rule(day) else 0))


def to_local(currency: str, when: datetime) -> datetime:
    """UTC instant -> naive home-market local time."""
    utc = when.astimezone(timezone.utc).replace(tzinfo=None)
    guess = utc + utc_offset(currency, utc.date())
    return utc + utc_offset(currency, guess.date())


def to_utc(currency: str, local: datetime) -> datetime:
    """Naive home-market local time -> aware UTC instant."""
    return (local - utc_offset(currency, local.date())).replace(tzinfo=timezone.utc)
