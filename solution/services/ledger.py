"""Interaction ledger service: pending event resolution and effective disposition computation."""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from solution.models import InteractionEvent, Lead
from solution.schemas import DispositionCatalogue


def resolve_pending_events(session: Session, catalogue: DispositionCatalogue) -> int:
    """Resolve unlinked (orphan) events against newly ingested leads and call relationships.

    Resolution occurs in two cooperative phases:
    1. Direct phone matching: events with phone_hash are matched to leads with (workflow_id, phone_hash).
    2. Call relationship matching: events without a direct lead (e.g. disposition.override or
       out-of-order events) are linked through other events sharing the same call_id.

    Repeats until convergence. Returns the total number of newly linked events.
    """
    session.flush()
    total_resolved = 0

    while True:
        resolved_in_pass = 0

        # Phase 1: Match unlinked events that have a phone_hash
        unlinked_with_phone = session.scalars(
            select(InteractionEvent).where(
                InteractionEvent.is_linked.is_(False),
                InteractionEvent.phone_hash.isnot(None),
            )
        ).all()

        for ev in unlinked_with_phone:
            assert ev.phone_hash is not None
            matched_lead = session.scalar(
                select(Lead).where(
                    Lead.workflow_id == ev.workflow_id,
                    Lead.phone_hash == ev.phone_hash,
                )
            )
            if matched_lead:
                ev.lead_id = matched_lead.id
                ev.is_linked = True
                # Check for unknown disposition code now that event is linked
                if ev.event_type.startswith("disposition."):
                    payload = json.loads(ev.payload_json or "{}")
                    code = payload.get("disposition_code", "")
                    if not catalogue.is_valid(ev.workflow_id, code):
                        ev.is_unknown_disposition = True
                    else:
                        ev.is_unknown_disposition = False
                resolved_in_pass += 1

        # Phase 2: Match unlinked events by call_id where another event for that call is linked
        # Find all unlinked events (this includes disposition.override which has no phone)
        unlinked_all = session.scalars(
            select(InteractionEvent).where(InteractionEvent.is_linked.is_(False))
        ).all()

        for ev in unlinked_all:
            # Look for any linked event with the same call_id
            linked_sibling = session.scalar(
                select(InteractionEvent).where(
                    InteractionEvent.call_id == ev.call_id,
                    InteractionEvent.is_linked.is_(True),
                    InteractionEvent.lead_id.isnot(None),
                )
            )
            if linked_sibling:
                ev.lead_id = linked_sibling.lead_id
                ev.is_linked = True
                if ev.event_type.startswith("disposition."):
                    payload = json.loads(ev.payload_json or "{}")
                    code = payload.get("disposition_code", "")
                    if not catalogue.is_valid(ev.workflow_id, code):
                        ev.is_unknown_disposition = True
                    else:
                        ev.is_unknown_disposition = False
                resolved_in_pass += 1

        total_resolved += resolved_in_pass
        session.flush()

        # Stop if no new events were resolved in this iteration
        if resolved_in_pass == 0:
            break

    return total_resolved


def compute_effective_disposition(
    events: List[InteractionEvent],
) -> Optional[str]:
    """Compute the effective disposition code for a call from its events.

    Rules:
    - Excludes unknown disposition events and unlinked events.
    - If one or more disposition.override events exist, use the latest override by occurred_at.
    - Otherwise use the latest disposition.set.
    - If neither exists, returns None.
    - Deterministic tie-breaking: if occurred_at timestamps are identical, tie-break by event_id ascending.
    """
    valid_overrides: List[Tuple[datetime, str, str]] = []
    valid_sets: List[Tuple[datetime, str, str]] = []

    for ev in events:
        if not ev.is_linked or ev.is_unknown_disposition:
            continue
        payload = json.loads(ev.payload_json or "{}")
        code = payload.get("disposition_code")
        if not code:
            continue

        if ev.event_type == "disposition.override":
            valid_overrides.append((ev.occurred_at, ev.event_id, code))
        elif ev.event_type == "disposition.set":
            valid_sets.append((ev.occurred_at, ev.event_id, code))

    if valid_overrides:
        # Sort by occurred_at ascending, tie-break by event_id ascending
        valid_overrides.sort(key=lambda x: (x[0], x[1]))
        return valid_overrides[-1][2]

    if valid_sets:
        valid_sets.sort(key=lambda x: (x[0], x[1]))
        return valid_sets[-1][2]

    return None
