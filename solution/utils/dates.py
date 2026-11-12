"""Date and timestamp utilities for UTC/IST handling and date parsing."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from dateutil import parser as dateutil_parser

from solution.config import IST, UTC

# Valid month names for DD-Mon-YYYY
MONTH_MAP = {
    "jan": "Jan",
    "feb": "Feb",
    "mar": "Mar",
    "apr": "Apr",
    "may": "May",
    "jun": "Jun",
    "jul": "Jul",
    "aug": "Aug",
    "sep": "Sep",
    "oct": "Oct",
    "nov": "Nov",
    "dec": "Dec",
}


def parse_timestamp(ts_str: str) -> datetime:
    """Parse an event timestamp string into an aware UTC datetime.

    Rules:
    - UTC timestamps ending in 'Z' (e.g. 2026-10-05T13:20:21Z).
    - Offset-aware ISO timestamps (e.g. 2026-10-05T18:50:21+05:30).
    - Naive timestamps (e.g. 2026-10-03 10:01:00 or 2026-10-03T10:01:00):
      Interpreted strictly as Asia/Kolkata (IST), never the machine local timezone.

    Returns aware UTC datetime.
    """
    s = ts_str.strip()
    if not s:
        raise ValueError("Timestamp string cannot be empty")

    if s.endswith("Z"):
        # Explicit UTC
        dt = datetime.fromisoformat(s[:-1] + "+00:00")
        return dt.astimezone(UTC)

    # Check if there is an explicit timezone offset like +05:30 or -04:00
    # Standard ISO-8601 offset check at the end
    has_offset = bool(re.search(r"[+-]\d{2}:?\d{2}$", s))
    if has_offset:
        dt = dateutil_parser.isoparse(s)
        return dt.astimezone(UTC)

    # Otherwise naive: interpret as IST
    dt = dateutil_parser.parse(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(UTC)


def to_ist(dt: datetime) -> datetime:
    """Convert an aware datetime to Asia/Kolkata (IST)."""
    if dt.tzinfo is None:
        # If naive, treat as UTC or IST? Always expect aware UTC internally
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def format_ist_iso(dt: datetime) -> str:
    """Format a datetime as ISO-8601 with +05:30 offset in IST.

    Example: 2026-10-14T17:55:24+05:30
    """
    ist_dt = to_ist(dt)
    return ist_dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")


def get_ist_date(dt: datetime) -> str:
    """Extract the YYYY-MM-DD date in IST."""
    ist_dt = to_ist(dt)
    return ist_dt.strftime("%Y-%m-%d")


def parse_date_field(val: str) -> Optional[str]:
    """Parse and validate a lead date string into canonical 'YYYY-MM-DD'.

    Accepted formats (strictly day-first when ambiguous):
    - YYYY-MM-DD
    - DD/MM/YYYY
    - DD-MM-YYYY
    - DD-Mon-YYYY (e.g. 01-Dec-2026, month is case-insensitive)

    Rejects impossible dates (e.g. 31/02/2026) and unparseable strings.
    Returns canonical 'YYYY-MM-DD' or None if invalid.
    """
    if not val:
        return None
    s = val.strip()
    if not s:
        return None

    # Check for DD-Mon-YYYY with case-insensitive month
    mon_match = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", s)
    if mon_match:
        d_str, m_str, y_str = mon_match.groups()
        m_lower = m_str.lower()
        if m_lower in MONTH_MAP:
            s_norm = f"{int(d_str):02d}-{MONTH_MAP[m_lower]}-{y_str}"
            try:
                dt = datetime.strptime(s_norm, "%d-%b-%Y")
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                return None
        return None

    formats = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            # Ensure year is reasonable and formatted
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None
