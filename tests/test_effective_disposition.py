"""Tests for effective disposition logic, overrides, and tie-breaking."""

import json
from datetime import datetime, timezone

from solution.models import InteractionEvent
from solution.services.ledger import compute_effective_disposition


def make_event(
    event_id: str,
    event_type: str,
    occurred_at_str: str,
    code: str,
    is_linked: bool = True,
    is_unknown: bool = False,
) -> InteractionEvent:
    dt = datetime.fromisoformat(occurred_at_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    return InteractionEvent(
        event_id=event_id,
        event_type=event_type,
        workflow_id="motor_renewal",
        call_id="call_001",
        occurred_at=dt,
        payload_json=json.dumps({"disposition_code": code}),
        is_linked=is_linked,
        is_unknown_disposition=is_unknown,
        created_at=dt,
    )


def test_single_disposition_set():
    """Single disposition.set establishes the effective disposition."""
    e1 = make_event("evt_01", "disposition.set", "2026-10-05T10:00:00Z", "callback_requested")
    assert compute_effective_disposition([e1]) == "callback_requested"


def test_multiple_disposition_sets():
    """Multiple disposition.sets resolve to the latest by occurred_at."""
    e1 = make_event("evt_01", "disposition.set", "2026-10-05T10:00:00Z", "callback_requested")
    e2 = make_event("evt_02", "disposition.set", "2026-10-05T10:05:00Z", "not_interested")
    assert compute_effective_disposition([e1, e2]) == "not_interested"
    # Even if list passed in reverse order:
    assert compute_effective_disposition([e2, e1]) == "not_interested"


def test_single_override_overrides_disposition_set():
    """A disposition.override takes precedence over disposition.set."""
    e1 = make_event("evt_01", "disposition.set", "2026-10-05T10:00:00Z", "not_interested")
    e2 = make_event("evt_02", "disposition.override", "2026-10-05T11:00:00Z", "renewed")
    assert compute_effective_disposition([e1, e2]) == "renewed"


def test_multiple_overrides():
    """Multiple overrides resolve to the latest override by occurred_at."""
    e1 = make_event("evt_01", "disposition.set", "2026-10-05T10:00:00Z", "not_interested")
    e2 = make_event("evt_02", "disposition.override", "2026-10-05T11:00:00Z", "payment_link_sent")
    e3 = make_event("evt_03", "disposition.override", "2026-10-05T12:00:00Z", "renewed")
    assert compute_effective_disposition([e1, e2, e3]) == "renewed"


def test_override_timestamp_earlier_than_set():
    """If an override exists, it takes precedence even if arrived out of order."""
    e1 = make_event("evt_01", "disposition.override", "2026-10-05T11:00:00Z", "renewed")
    e2 = make_event("evt_02", "disposition.set", "2026-10-05T10:00:00Z", "not_interested")
    # Both in list
    assert compute_effective_disposition([e1, e2]) == "renewed"


def test_unknown_disposition_ignored():
    """Unknown disposition codes flagged with is_unknown_disposition=True are ignored."""
    e1 = make_event("evt_01", "disposition.set", "2026-10-05T10:00:00Z", "renewed")
    # Bad override with unknown code
    e2 = make_event(
        "evt_02", "disposition.override", "2026-10-05T11:00:00Z", "UNKNOWN_CODE", is_unknown=True
    )
    # The unknown override is ignored; the valid disposition.set remains effective
    assert compute_effective_disposition([e1, e2]) == "renewed"


def test_equal_timestamps_deterministic_tie_breaker():
    """When occurred_at timestamps are equal, tie-break deterministically by event_id ascending."""
    e1 = make_event("evt_01", "disposition.set", "2026-10-05T10:00:00Z", "not_interested")
    e2 = make_event("evt_02", "disposition.set", "2026-10-05T10:00:00Z", "renewed")
    # 'evt_02' > 'evt_01', so evt_02 wins deterministically
    assert compute_effective_disposition([e1, e2]) == "renewed"
    assert compute_effective_disposition([e2, e1]) == "renewed"
