"""Data aggregation service for call metrics, lead status, buckets, and overviews."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from solution.models import InteractionEvent, Lead
from solution.schemas import DispositionCatalogue
from solution.services.ledger import compute_effective_disposition
from solution.utils.dates import format_ist_iso, get_ist_date


@dataclass
class CallFact:
    """Derived facts for a single placed call."""

    call_id: str
    workflow_id: str
    lead_id: int
    initiated_at: datetime
    ist_date: str
    is_connected: bool = False
    has_telephony_failed: bool = False
    effective_disposition: Optional[str] = None
    is_terminal: bool = False
    is_success: bool = False


@dataclass
class LeadFact:
    """Aggregated metrics and bucket classification for a single customer lead."""

    lead_id: int
    workflow_id: str
    batch_id: str
    external_ref: str
    phone_masked: str
    total_calls: int = 0
    connected_calls: int = 0
    last_call_at: str = ""
    current_disposition: str = ""
    ever_success: bool = False
    bucket: str = "yet_to_reach_out"


def build_call_facts(
    session: Session,
    catalogue: DispositionCatalogue,
) -> Dict[str, CallFact]:
    """Aggregate linked events into call facts.

    A call is counted if and only if its valid, linked call.initiated event exists.
    """
    # Fetch all linked events
    events = session.scalars(
        select(InteractionEvent)
        .where(InteractionEvent.is_linked.is_(True))
        .order_by(InteractionEvent.occurred_at, InteractionEvent.event_id)
    ).all()

    # Group events by call_id
    call_events_map: Dict[str, List[InteractionEvent]] = {}
    for ev in events:
        call_events_map.setdefault(ev.call_id, []).append(ev)

    call_facts: Dict[str, CallFact] = {}

    for call_id, ev_list in call_events_map.items():
        # Find call.initiated event
        init_ev = next((e for e in ev_list if e.event_type == "call.initiated"), None)
        if init_ev is None or init_ev.lead_id is None:
            # Not a valid call or not linked to a lead
            continue

        wf_id = init_ev.workflow_id
        lead_id = init_ev.lead_id
        init_at = init_ev.occurred_at
        ist_date = get_ist_date(init_at)

        is_connected = any(e.event_type == "call.connected" for e in ev_list)
        has_telephony_failed = any(e.event_type == "call.telephony_failed" for e in ev_list)

        eff_disp = compute_effective_disposition(ev_list)
        is_terminal = catalogue.is_terminal(wf_id, eff_disp) if eff_disp else False
        is_success = catalogue.is_success(wf_id, eff_disp) if eff_disp else False

        call_facts[call_id] = CallFact(
            call_id=call_id,
            workflow_id=wf_id,
            lead_id=lead_id,
            initiated_at=init_at,
            ist_date=ist_date,
            is_connected=is_connected,
            has_telephony_failed=has_telephony_failed,
            effective_disposition=eff_disp,
            is_terminal=is_terminal,
            is_success=is_success,
        )

    return call_facts


def build_lead_facts(
    session: Session,
    call_facts: Dict[str, CallFact],
) -> List[LeadFact]:
    """Build aggregated lead facts and assign buckets for each customer lead.

    Bucket precedence:
    1. yet_to_reach_out: total_calls == 0
    2. completed: any call has effective terminal disposition (is_terminal=true)
    3. in_progress_telephony_failed: every call has a call.telephony_failed event
    4. in_progress_dialed: all remaining cases
    """
    leads = session.scalars(select(Lead)).all()

    # Group call facts by lead_id
    lead_calls_map: Dict[int, List[CallFact]] = {}
    for call in call_facts.values():
        lead_calls_map.setdefault(call.lead_id, []).append(call)

    lead_facts: List[LeadFact] = []

    for lead in leads:
        lead_calls = lead_calls_map.get(lead.id, [])
        total_calls = len(lead_calls)
        connected_calls = sum(1 for c in lead_calls if c.is_connected)

        # Sort calls by initiated_at ascending, tie-break by call_id
        lead_calls.sort(key=lambda c: (c.initiated_at, c.call_id))

        last_call_at = ""
        if lead_calls:
            latest_call = lead_calls[-1]
            last_call_at = format_ist_iso(latest_call.initiated_at)

        # Current disposition: effective disposition of the most recent call
        # (by initiated time) that has one. Empty if none.
        current_disp = ""
        for call in reversed(lead_calls):
            if call.effective_disposition:
                current_disp = call.effective_disposition
                break

        ever_success = any(c.is_success for c in lead_calls)

        # Bucket classification in exact priority order
        if total_calls == 0:
            bucket = "yet_to_reach_out"
        elif any(c.is_terminal for c in lead_calls):
            bucket = "completed"
        elif all(c.has_telephony_failed for c in lead_calls):
            bucket = "in_progress_telephony_failed"
        else:
            bucket = "in_progress_dialed"

        phone_masked = f"+91******{lead.phone_last4}"

        lead_facts.append(
            LeadFact(
                lead_id=lead.id,
                workflow_id=lead.workflow_id,
                batch_id=lead.batch_id,
                external_ref=lead.external_ref,
                phone_masked=phone_masked,
                total_calls=total_calls,
                connected_calls=connected_calls,
                last_call_at=last_call_at,
                current_disposition=current_disp,
                ever_success=ever_success,
                bucket=bucket,
            )
        )

    return lead_facts
