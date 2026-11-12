"""Field-level value normalisation for strings, phones, dates, decimals, and enums."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

from solution.schemas import FieldDefinition
from solution.utils.dates import parse_date_field
from solution.utils.phones import normalise_phone

DECIMAL_REGEX = re.compile(
    r"^(?:₹|rs\.?|inr)?\s*([+-]?[\d,]+(?:\.\d+)?)$",
    re.IGNORECASE,
)


def normalise_string(val: Optional[str]) -> Optional[str]:
    """Trim string value. Empty string returns None."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def normalise_decimal(val: Optional[str]) -> Optional[float]:
    """Parse and normalise a decimal / currency string.

    Rules:
    - Optionally prefixed by ₹, Rs, Rs., or INR (case-insensitive, optional space).
    - Commas are thousands separators.
    - Must be >= 0 (negative values are rejected).
    - Round to 2 decimal places.

    Returns float rounded to 2 places, or None if invalid.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None

    match = DECIMAL_REGEX.match(s)
    if not match:
        return None

    num_str = match.group(1).replace(",", "")
    try:
        f = float(num_str)
    except ValueError:
        return None

    if f < 0:
        return None  # Negative amounts are invalid per spec

    return round(f, 2)


def normalise_enum(val: Optional[str], allowed_values: List[str]) -> Optional[str]:
    """Normalise enum value: compare case-insensitively, store lowercase.

    Returns lowercase enum value if matched, else None.
    """
    if val is None:
        return None
    s = str(val).strip().lower()
    if not s:
        return None

    for allowed in allowed_values:
        if s == allowed.strip().lower():
            return s
    return None


def normalise_field_value(field_def: FieldDefinition, raw_val: Optional[str]) -> Tuple[Any, bool]:
    """Normalise a raw field value according to its FieldDefinition.

    Returns:
    - (normalised_val, is_valid)
    - If raw_val is None or empty: returns (None, True) because presence/required is checked separately.
    - If raw_val is present but invalid: returns (None, False).
    """
    if raw_val is None:
        return None, True
    trimmed = str(raw_val).strip()
    if not trimmed:
        return None, True

    ftype = field_def.type
    if ftype == "string":
        return trimmed, True
    elif ftype == "phone":
        norm = normalise_phone(trimmed)
        return (norm, True) if norm is not None else (None, False)
    elif ftype == "date":
        norm = parse_date_field(trimmed)
        return (norm, True) if norm is not None else (None, False)
    elif ftype == "decimal":
        norm = normalise_decimal(trimmed)
        return (norm, True) if norm is not None else (None, False)
    elif ftype == "enum":
        norm = normalise_enum(trimmed, field_def.values)
        return (norm, True) if norm is not None else (None, False)
    else:
        # Unknown type falls back to trimmed string
        return trimmed, True
