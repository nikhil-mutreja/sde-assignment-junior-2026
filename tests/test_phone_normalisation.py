"""Tests for phone normalisation, validation, and masking."""

import pytest

from solution.utils.phones import mask_phone, normalise_phone


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("91-98765-43210", "+919876543210"),
        ("(+91) 9876543210", "+919876543210"),
        ("+919876543210", "+919876543210"),
        ("6123456789", "+916123456789"),
        ("7123456789", "+917123456789"),
        ("8123456789", "+918123456789"),
    ],
)
def test_valid_phone_formats(raw: str, expected: str):
    """Verify all valid Indian mobile phone formats are normalised canonically."""
    assert normalise_phone(raw) == expected


@pytest.mark.parametrize(
    "invalid_raw",
    [
        "987654321",  # 9 digits
        "98765432100",  # 11 digits not starting with 0
        "5987654321",  # 10 digits starting with 5 (invalid prefix)
        "1987654321",  # 10 digits starting with 1
        "022-2456-7890",  # Landline (drops 0, leaves 2224567890 starting with 2)
        "+1 415 555 0134",  # US number
        "N/A",  # Non-digit string
        "",  # Empty string
        None,  # None
        "          ",  # Whitespace
        "009876543210",  # Multiple leading zeroes
    ],
)
def test_invalid_phone_formats(invalid_raw):
    """Verify invalid phone numbers return None."""
    assert normalise_phone(invalid_raw) is None


def test_mask_phone():
    """Verify phone masking produces +91****** followed by last 4 digits."""
    assert mask_phone("+919876543210") == "+91******3210"
    assert mask_phone("+917718750036") == "+91******0036"
    assert mask_phone("") == ""
