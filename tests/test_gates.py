"""Tests for fx_scalper/gates.py: Friday cutoff, forced-flat time, news blackout edges.

All datetimes are timezone-aware UTC. Session window is widened to the whole
day in the Friday tests so that only the Friday cutoff decides the outcome.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fx_scalper.gates import entry_allowed
from fx_scalper.settings import Settings

UTC = timezone.utc

# 2026-11-06 is a Friday; 2026-11-05 is a Thursday; 2026-11-09 is a Monday.
FRIDAY = (2026, 11, 6)
THURSDAY = (2026, 11, 5)
MONDAY = (2026, 11, 9)


def _settings(tmp_path, *, events=None, blackout_exists=True, **overrides):
    path = tmp_path / "blackout.json"
    if blackout_exists:
        path.write_text(json.dumps(events or []), encoding="utf-8")
    base = dict(
        api_token="tok-test",
        account_id="acct-test",
        session_start_utc="00:00",
        session_end_utc="23:59:59",
        blackout_file=str(path),
        news_pause_minutes=15,
        no_entry_friday_utc="20:00",
        forced_flat_friday_utc="20:45",
    )
    base.update(overrides)
    return Settings(**base)


def _utc(y, m, d, hh, mm, ss=0, us=0):
    return datetime(y, m, d, hh, mm, ss, us, tzinfo=UTC)


# ---------------------------------------------------------------- Friday cutoff

def test_friday_just_before_cutoff_is_allowed(tmp_path):
    s = _settings(tmp_path)
    assert entry_allowed(_utc(*FRIDAY, 19, 59, 59, 999999), s) is True


def test_friday_at_cutoff_is_blocked(tmp_path):
    s = _settings(tmp_path)
    assert entry_allowed(_utc(*FRIDAY, 20, 0, 0), s) is False


def test_friday_after_cutoff_is_blocked(tmp_path):
    s = _settings(tmp_path)
    assert entry_allowed(_utc(*FRIDAY, 20, 30, 0), s) is False


def test_friday_cutoff_does_not_apply_on_thursday(tmp_path):
    s = _settings(tmp_path)
    assert entry_allowed(_utc(*THURSDAY, 20, 0, 0), s) is True


def test_friday_cutoff_is_evaluated_in_utc(tmp_path):
    # 2026-11-06 19:30 UTC is Friday 21:30 at UTC+2, which is after the UTC
    # cutoff; the gate must use UTC, not the caller's local clock.
    s = _settings(tmp_path)
    local = datetime(2026, 11, 6, 21, 30, tzinfo=timezone(timedelta(hours=2)))
    assert local.astimezone(UTC) == _utc(2026, 11, 6, 19, 30)
    assert entry_allowed(local, s) is True  # 19:30 UTC is before 20:00 UTC


def test_naive_datetime_is_rejected(tmp_path):
    s = _settings(tmp_path)
    assert entry_allowed(datetime(2026, 11, 9, 14, 0), s) is False


# ---------------------------------------------------------------- Forced flat

def test_forced_flat_time_is_configured_after_friday_cutoff(tmp_path):
    # Forced-flat is a configured time (spec section 8). gates.py does not yet
    # enforce it, so this test checks only the configured value and ordering.
    s = _settings(tmp_path)
    assert s.forced_flat_friday_utc == "20:45"
    assert s.forced_flat_friday_utc > s.no_entry_friday_utc


def test_default_settings_carry_spec_friday_times():
    from fx_scalper.settings import Settings as S

    d = S(api_token="x", account_id="y")
    assert d.no_entry_friday_utc == "20:00"
    assert d.forced_flat_friday_utc == "20:45"


# ---------------------------------------------------------------- News blackout

# Event at 14:00 UTC on Monday with a 15-minute pause: blocked window is
# [13:45:00, 14:15:00] inclusive on both edges (gates.py uses <= on both ends).
EVENT = "2026-11-09T14:00:00Z"


def _blackout(tmp_path):
    return _settings(tmp_path, events=[{"time": EVENT}])


def test_blackout_just_before_start_is_allowed(tmp_path):
    s = _blackout(tmp_path)
    assert entry_allowed(_utc(*MONDAY, 13, 44, 59, 999999), s) is True


def test_blackout_at_start_is_blocked(tmp_path):
    s = _blackout(tmp_path)
    assert entry_allowed(_utc(*MONDAY, 13, 45, 0), s) is False


def test_blackout_inside_is_blocked(tmp_path):
    s = _blackout(tmp_path)
    assert entry_allowed(_utc(*MONDAY, 14, 0, 0), s) is False


def test_blackout_at_end_is_blocked(tmp_path):
    s = _blackout(tmp_path)
    assert entry_allowed(_utc(*MONDAY, 14, 15, 0), s) is False


def test_blackout_just_after_end_is_allowed(tmp_path):
    s = _blackout(tmp_path)
    assert entry_allowed(_utc(*MONDAY, 14, 15, 0, 1), s) is True


def test_blackout_boundary_respects_utc_offset_input(tmp_path):
    # 15:45 at UTC+2 is 13:45 UTC: the start edge, so it must be blocked.
    s = _blackout(tmp_path)
    local = datetime(2026, 11, 9, 15, 45, 0, tzinfo=timezone(timedelta(hours=2)))
    assert entry_allowed(local, s) is False


def test_missing_blackout_file_means_no_blackout(tmp_path):
    s = _settings(tmp_path, blackout_exists=False)
    assert entry_allowed(_utc(*MONDAY, 14, 0, 0), s) is True


def test_unreadable_blackout_data_fails_closed(tmp_path):
    path = tmp_path / "blackout.json"
    path.write_text("not json", encoding="utf-8")
    s = _settings(tmp_path, blackout_file=str(path))
    assert entry_allowed(_utc(*MONDAY, 10, 0, 0), s) is False
