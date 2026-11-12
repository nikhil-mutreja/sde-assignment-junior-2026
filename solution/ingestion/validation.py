"""Row-level validation and rejection handling against workflow schemas."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from solution.ingestion.normalisation import normalise_field_value
from solution.schemas import WorkflowSchema
from solution.utils.phones import normalise_phone


def validate_lead_row(
    schema: WorkflowSchema,
    row_dict: Dict[str, str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str], int]:
    """Validate a mapped CSV lead row against the workflow schema.

    Walks schema fields in exact schema order. The first failure becomes the row's
    single rejection reason.

    Rejection rules:
    - Required field is missing (empty/whitespace/absent) -> 'missing_required:<field_name>'
    - Phone is present but invalid -> 'invalid_phone'

    Optional field rules:
    - If present but invalid -> store null, increment field_warnings by 1, do not reject.

    Returns:
    - (normalised_data, rejection_reason, field_warnings_count)
    - If rejected: normalised_data is None, rejection_reason is set, warnings is 0.
    - If valid: normalised_data is populated, rejection_reason is None, warnings >= 0.
    """
    normalised_data: Dict[str, Any] = {}
    field_warnings = 0

    for field in schema.fields:
        raw_val = row_dict.get(field.name)
        val_str = str(raw_val).strip() if raw_val is not None else ""
        is_empty = val_str == ""

        if is_empty:
            if field.required:
                # Required field is missing
                return None, f"missing_required:{field.name}", 0
            else:
                normalised_data[field.name] = None
                continue

        # Value is non-empty
        if field.type == "phone":
            norm_phone = normalise_phone(val_str)
            if norm_phone is None:
                # Present but invalid phone triggers row rejection
                return None, "invalid_phone", 0
            normalised_data[field.name] = norm_phone
        else:
            norm_val, is_valid = normalise_field_value(field, val_str)
            if not is_valid:
                # Invalid optional field: store null and count one field warning
                normalised_data[field.name] = None
                field_warnings += 1
            else:
                normalised_data[field.name] = norm_val

    return normalised_data, None, field_warnings
