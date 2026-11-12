"""Tests for lead CSV header mapping, validation, and rejection reasons."""

from solution.ingestion.normalisation import normalise_decimal, normalise_enum
from solution.ingestion.validation import validate_lead_row
from solution.schemas import WorkflowSchema


def test_header_alias_mapping(motor_schema: WorkflowSchema):
    """Test header alias mapping with various case, whitespace, and underscore styles."""
    input_headers = [
        "Insured Name",
        "Mobile No",
        "Policy No",
        "Expiry Date",
        "Premium",
        "Language",
        "extra_unknown_col",
    ]
    mapping = motor_schema.map_headers(input_headers)
    assert mapping["Insured Name"] == "customer_name"
    assert mapping["Mobile No"] == "phone"
    assert mapping["Policy No"] == "policy_number"
    assert mapping["Expiry Date"] == "policy_expiry"
    assert mapping["Premium"] == "premium_amount"
    assert mapping["Language"] == "preferred_language"
    assert "extra_unknown_col" not in mapping


def test_missing_required_field_customer_name(motor_schema: WorkflowSchema):
    """Missing required customer_name triggers missing_required:customer_name."""
    row = {
        "customer_name": "",
        "phone": "9876543210",
        "policy_number": "POL-1234",
    }
    data, reason, warnings = validate_lead_row(motor_schema, row)
    assert data is None
    assert reason == "missing_required:customer_name"
    assert warnings == 0


def test_missing_required_field_policy_number(motor_schema: WorkflowSchema):
    """Missing policy_number triggers missing_required:policy_number."""
    row = {
        "customer_name": "Aarav Sharma",
        "phone": "9876543210",
        "policy_number": "   ",
    }
    data, reason, warnings = validate_lead_row(motor_schema, row)
    assert data is None
    assert reason == "missing_required:policy_number"


def test_invalid_phone_rejection(motor_schema: WorkflowSchema):
    """Invalid phone format triggers invalid_phone rejection."""
    row = {
        "customer_name": "Aarav Sharma",
        "phone": "12345",  # Invalid phone
        "policy_number": "POL-1234",
    }
    data, reason, warnings = validate_lead_row(motor_schema, row)
    assert data is None
    assert reason == "invalid_phone"


def test_schema_order_precedence(motor_schema: WorkflowSchema):
    """If both customer_name and phone are invalid, first in schema order (customer_name) is returned."""
    row = {
        "customer_name": "",
        "phone": "12345",
        "policy_number": "",
    }
    data, reason, warnings = validate_lead_row(motor_schema, row)
    assert data is None
    assert reason == "missing_required:customer_name"


def test_optional_field_warning(motor_schema: WorkflowSchema):
    """Invalid optional field becomes null and increments field_warnings without rejecting row."""
    row = {
        "customer_name": "Aarav Sharma",
        "phone": "9876543210",
        "policy_number": "POL-1234",
        "policy_expiry": "31/02/2026",  # Invalid date
        "premium_amount": "invalid_amount",  # Invalid decimal
        "preferred_language": "invalid_lang",  # Invalid enum
    }
    data, reason, warnings = validate_lead_row(motor_schema, row)
    assert reason is None
    assert data is not None
    assert data["policy_expiry"] is None
    assert data["premium_amount"] is None
    assert data["preferred_language"] is None
    assert warnings == 3  # 3 invalid optional fields


def test_decimal_normalisation():
    """Verify various currency and decimal formats."""
    assert normalise_decimal("₹12,000") == 12000.00
    assert normalise_decimal("Rs. 4,500.50") == 4500.50
    assert normalise_decimal("INR 38000.0") == 38000.00
    assert normalise_decimal("150.756") == 150.76  # Rounded to 2 decimal places
    assert normalise_decimal("-500") is None  # Negative rejected
    assert normalise_decimal("N/A") is None


def test_enum_case_insensitivity():
    """Verify enum values are compared case-insensitively and stored lowercase."""
    allowed = ["en", "hi", "ta", "kn", "te", "mr"]
    assert normalise_enum("EN", allowed) == "en"
    assert normalise_enum(" Hi ", allowed) == "hi"
    assert normalise_enum("French", allowed) is None
