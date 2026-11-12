"""Phone number validation, canonicalisation, and masking utilities."""

from __future__ import annotations

import re
from typing import Optional


def normalise_phone(raw: Optional[str]) -> Optional[str]:
    """Validate and normalise an Indian mobile phone number.

    Rules:
    - Strip all non-digit characters.
    - If 12 digits starting with '91', drop '91'.
    - If 11 digits starting with '0', drop '0'.
    - Must be exactly 10 digits starting with 6, 7, 8, or 9.
    - Stored canonically as '+91XXXXXXXXXX'.

    Returns canonical phone string or None if invalid.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None

    digits = re.sub(r"\D", "", s)

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"

    return None


def mask_phone(canonical_phone: str) -> str:
    """Mask a phone number for report output: '+91******' followed by the last 4 digits.

    Example: '+919876543210' -> '+91******3210'.
    """
    if not canonical_phone:
        return ""
    digits = re.sub(r"\D", "", canonical_phone)
    if len(digits) >= 4:
        return f"+91******{digits[-4:]}"
    return f"+91******{digits}"
