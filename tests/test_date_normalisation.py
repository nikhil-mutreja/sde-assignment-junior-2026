"""Tests for date parsing, timestamp interpretation (UTC, offset, naive IST), and formatting."""

from datetime import datetime, timezone

import pytest

from solution.config import UTC
from solution.utils.dates import (
    format_ist_iso,
    get_ist_date,
    parse_date_field,
    parse_timestamp,
)


@pytest.mark.parametrize(
    "raw_date,expected",
    [
        ("2026-10-15", "2026-10-15"),
        ("15/10/2026", "2026-10-15"),
        ("15-10-2026", "2026-10-15"),
        ("01-Dec-2026", "2026-12-01"),
        ("01-dec-2026", "2026-12-01"),
        ("01-DEC-2026", "2026-12-01"),
        ("29/02/2024", "2024-02-29"),  # Leap year
    ],
)
def test_valid_date_parsing(raw_date: str, expected: str):
    """Test valid date parsing in all 4 supported formats."""
    assert parse_date_field(raw_date) == expected


@pytest.mark.parametrize(
    "invalid_date",
    [
        "31/02/2026",  # Impossible date
        "29/02/2026",  # Non-leap year
        "31/04/2026",  # April has 30 days
        "2026-13-01",  # Month 13
        "soon",  # Text
        "TBD",  # Text
        "15-Foo-2026",  # Invalid month name
        "",
        None,
    ],
)
def test_impossible_and_invalid_dates(invalid_date):
    """Test rejection of impossible or unparseable dates."""
    assert parse_date_field(invalid_date) is None


def test_timestamp_utc_ending_in_z():
    """Verify UTC timestamps ending in Z are parsed into aware UTC."""
    ts = "2026-10-05T13:20:21Z"
    dt = parse_timestamp(ts)
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2026
    assert dt.month == 10
    assert dt.day == 5
    assert dt.hour == 13
    assert dt.minute == 20
    assert dt.second == 21


def test_timestamp_with_offset():
    """Verify offset-aware timestamps are parsed and converted to UTC."""
    ts = "2026-10-05T18:50:21+05:30"
    dt = parse_timestamp(ts)
    assert dt.tzinfo == timezone.utc
    # 18:50:21 IST is 13:20:21 UTC
    assert dt.hour == 13
    assert dt.minute == 20
    assert dt.second == 21


def test_timestamp_naive_interpreted_as_ist():
    """Verify naive timestamps are strictly interpreted as IST, never local machine time."""
    ts = "2026-10-05 18:50:21"
    dt = parse_timestamp(ts)
    assert dt.tzinfo == timezone.utc
    # 18:50:21 IST = 13:20:21 UTC
    assert dt.hour == 13
    assert dt.minute == 20
    assert dt.second == 21


def test_ist_midnight_boundary_and_date():
    """Verify conversion to IST date near midnight boundary."""
    # 2026-10-05 18:45:00 UTC = 2026-10-06 00:15:00 IST
    utc_dt = datetime(2026, 10, 5, 18, 45, 0, tzinfo=UTC)
    ist_date = get_ist_date(utc_dt)
    assert ist_date == "2026-10-06"
    assert format_ist_iso(utc_dt) == "2026-10-06T00:15:00+05:30"
