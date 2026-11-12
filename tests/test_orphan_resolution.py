"""Tests for orphan event handling and deferred resolution across drops."""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from solution.ingestion.events import ingest_events_jsonl
from solution.ingestion.leads import ingest_lead_csv
from solution.models import InteractionEvent
from solution.schemas import DispositionCatalogue, WorkflowSchema
from solution.services.data_quality import compute_data_quality_metrics
from solution.services.ledger import resolve_pending_events


def test_orphan_resolution_across_drops(
    db_session: Session,
    motor_schema: WorkflowSchema,
    catalogue: DispositionCatalogue,
    temp_dir: Path,
):
    """Verify an event arriving before its lead is initially an orphan, then resolves when the lead arrives."""
    # Step 1: Drop 1 has call events for a phone not yet uploaded
    drop1_events_path = temp_dir / "drop1_events.jsonl"
    drop1_events_path.write_text(
        json.dumps(
            {
                "event_id": "evt_drop1_001",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_drop1_001",
                "occurred_at": "2026-10-05T10:00:00Z",
                "phone": "9876543210",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    ingest_events_jsonl(db_session, drop1_events_path, catalogue)

    # In drop 1, the event cannot link to any lead
    ev = db_session.scalar(
        select(InteractionEvent).where(InteractionEvent.event_id == "evt_drop1_001")
    )
    assert ev is not None
    assert ev.is_linked is False
    assert ev.lead_id is None

    dq1 = compute_data_quality_metrics(db_session)
    assert dq1["orphan_events"] == 1

    # Step 2: Drop 2 uploads the lead for 9876543210
    drop2_leads_path = temp_dir / "drop2_batch_003.csv"
    drop2_leads_path.write_text(
        "customer_name,phone,policy_number\nAarav Sharma,9876543210,MOT/2026/000001\n",
        encoding="utf-8",
    )

    ingest_lead_csv(db_session, drop2_leads_path, "motor_renewal", "batch_003", motor_schema)

    # Run pending resolution (as done during drop ingestion)
    resolved_count = resolve_pending_events(db_session, catalogue)
    assert resolved_count == 1

    # Now the drop 1 event must be linked to the newly arrived lead!
    db_session.expire_all()
    ev_after = db_session.scalar(
        select(InteractionEvent).where(InteractionEvent.event_id == "evt_drop1_001")
    )
    assert ev_after is not None
    assert ev_after.is_linked is True
    assert ev_after.lead_id is not None

    dq2 = compute_data_quality_metrics(db_session)
    # Orphan count drops to 0!
    assert dq2["orphan_events"] == 0


def test_disposition_override_orphan_resolution(
    db_session: Session,
    motor_schema: WorkflowSchema,
    catalogue: DispositionCatalogue,
    temp_dir: Path,
):
    """Verify disposition.override (which has no phone) resolves via call_id to the lead."""
    # Lead uploaded
    lead_path = temp_dir / "lead.csv"
    lead_path.write_text(
        "customer_name,phone,policy_number\nPriya Iyer,9876543212,MOT/2026/000005\n",
        encoding="utf-8",
    )
    ingest_lead_csv(db_session, lead_path, "motor_renewal", "batch_001", motor_schema)

    # Override arrives FIRST (no phone, call_005)
    override_path = temp_dir / "override.jsonl"
    override_path.write_text(
        json.dumps(
            {
                "event_id": "evt_ovr_001",
                "event_type": "disposition.override",
                "workflow_id": "motor_renewal",
                "call_id": "call_005",
                "occurred_at": "2026-10-06T12:00:00Z",
                "payload": {"disposition_code": "renewed", "reason": "qa"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ingest_events_jsonl(db_session, override_path, catalogue)

    # Override is initially an orphan because no other call_005 events exist yet
    ev_ovr = db_session.scalar(
        select(InteractionEvent).where(InteractionEvent.event_id == "evt_ovr_001")
    )
    assert ev_ovr is not None
    assert ev_ovr.is_linked is False

    # Now call.initiated arrives with phone 9876543212
    init_path = temp_dir / "init.jsonl"
    init_path.write_text(
        json.dumps(
            {
                "event_id": "evt_init_001",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_005",
                "occurred_at": "2026-10-06T11:00:00Z",
                "phone": "9876543212",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ingest_events_jsonl(db_session, init_path, catalogue)

    # Both events must now be linked to Priya's lead
    db_session.expire_all()
    ev_ovr_after = db_session.scalar(
        select(InteractionEvent).where(InteractionEvent.event_id == "evt_ovr_001")
    )
    ev_init = db_session.scalar(
        select(InteractionEvent).where(InteractionEvent.event_id == "evt_init_001")
    )
    assert ev_init.is_linked is True
    assert ev_ovr_after.is_linked is True
    assert ev_ovr_after.lead_id == ev_init.lead_id
