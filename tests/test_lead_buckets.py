"""Tests for lead bucket classification precedence."""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from solution.ingestion.events import ingest_events_jsonl
from solution.ingestion.leads import ingest_lead_csv
from solution.reporting.aggregator import build_call_facts, build_lead_facts
from solution.schemas import DispositionCatalogue, WorkflowSchema


def test_lead_bucket_classification_precedence(
    db_session: Session,
    motor_schema: WorkflowSchema,
    catalogue: DispositionCatalogue,
    temp_dir: Path,
):
    """Verify the four lead buckets and their strict precedence:
    1. yet_to_reach_out: 0 calls
    2. completed: any call has effective terminal disposition
    3. in_progress_telephony_failed: every call has call.telephony_failed
    4. in_progress_dialed: all remaining cases
    """
    # 4 leads:
    # Lead 1: 0 calls -> yet_to_reach_out
    # Lead 2: 1 call completed (renewed is terminal) -> completed
    # Lead 3: 2 calls, both telephony_failed -> in_progress_telephony_failed
    # Lead 4: 1 call connected, callback_requested (non-terminal) -> in_progress_dialed
    csv_path = temp_dir / "leads.csv"
    csv_path.write_text(
        "customer_name,phone,policy_number\n"
        "Lead One,9876543211,MOT/001\n"
        "Lead Two,9876543212,MOT/002\n"
        "Lead Three,9876543213,MOT/003\n"
        "Lead Four,9876543214,MOT/004\n",
        encoding="utf-8",
    )
    ingest_lead_csv(db_session, csv_path, "motor_renewal", "batch_001", motor_schema)

    events_path = temp_dir / "events.jsonl"
    lines = [
        # Calls for Lead Two: initiated + connected + disposition.set(renewed) [terminal]
        json.dumps(
            {
                "event_id": "e_l2_01",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c_l2",
                "occurred_at": "2026-10-05T10:00:00Z",
                "phone": "9876543212",
            }
        ),
        json.dumps(
            {
                "event_id": "e_l2_02",
                "event_type": "call.connected",
                "workflow_id": "motor_renewal",
                "call_id": "c_l2",
                "occurred_at": "2026-10-05T10:00:10Z",
                "phone": "9876543212",
            }
        ),
        json.dumps(
            {
                "event_id": "e_l2_03",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "c_l2",
                "occurred_at": "2026-10-05T10:02:00Z",
                "phone": "9876543212",
                "payload": {"disposition_code": "renewed"},
            }
        ),
        # Calls for Lead Three: call 1 failed, call 2 failed
        json.dumps(
            {
                "event_id": "e_l3_01",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c_l3_1",
                "occurred_at": "2026-10-05T11:00:00Z",
                "phone": "9876543213",
            }
        ),
        json.dumps(
            {
                "event_id": "e_l3_02",
                "event_type": "call.telephony_failed",
                "workflow_id": "motor_renewal",
                "call_id": "c_l3_1",
                "occurred_at": "2026-10-05T11:00:05Z",
                "phone": "9876543213",
                "payload": {"reason": "sip_503"},
            }
        ),
        json.dumps(
            {
                "event_id": "e_l3_03",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c_l3_2",
                "occurred_at": "2026-10-05T12:00:00Z",
                "phone": "9876543213",
            }
        ),
        json.dumps(
            {
                "event_id": "e_l3_04",
                "event_type": "call.telephony_failed",
                "workflow_id": "motor_renewal",
                "call_id": "c_l3_2",
                "occurred_at": "2026-10-05T12:00:05Z",
                "phone": "9876543213",
                "payload": {"reason": "carrier_rejected"},
            }
        ),
        # Calls for Lead Four: call connected, callback_requested (non-terminal)
        json.dumps(
            {
                "event_id": "e_l4_01",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c_l4",
                "occurred_at": "2026-10-05T13:00:00Z",
                "phone": "9876543214",
            }
        ),
        json.dumps(
            {
                "event_id": "e_l4_02",
                "event_type": "call.connected",
                "workflow_id": "motor_renewal",
                "call_id": "c_l4",
                "occurred_at": "2026-10-05T13:00:15Z",
                "phone": "9876543214",
            }
        ),
        json.dumps(
            {
                "event_id": "e_l4_03",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "c_l4",
                "occurred_at": "2026-10-05T13:03:00Z",
                "phone": "9876543214",
                "payload": {"disposition_code": "callback_requested"},
            }
        ),
    ]
    events_path.write_text("\n".join(lines), encoding="utf-8")
    ingest_events_jsonl(db_session, events_path, catalogue)

    call_facts = build_call_facts(db_session, catalogue)
    lead_facts = build_lead_facts(db_session, call_facts)

    facts_by_ref = {lf.external_ref: lf for lf in lead_facts}

    assert facts_by_ref["MOT/001"].bucket == "yet_to_reach_out"
    assert facts_by_ref["MOT/002"].bucket == "completed"
    assert facts_by_ref["MOT/003"].bucket == "in_progress_telephony_failed"
    assert facts_by_ref["MOT/004"].bucket == "in_progress_dialed"


def test_completed_beats_telephony_failed_precedence(
    db_session: Session,
    motor_schema: WorkflowSchema,
    catalogue: DispositionCatalogue,
    temp_dir: Path,
):
    """If a lead had one call that telephony failed and another call that completed, bucket is completed."""
    csv_path = temp_dir / "leads_prec.csv"
    csv_path.write_text(
        "customer_name,phone,policy_number\nLead Five,9876543215,MOT/005\n",
        encoding="utf-8",
    )
    ingest_lead_csv(db_session, csv_path, "motor_renewal", "batch_001", motor_schema)

    events_path = temp_dir / "events_prec.jsonl"
    lines = [
        # Call 1: failed
        json.dumps(
            {
                "event_id": "e_prec_01",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c_prec_1",
                "occurred_at": "2026-10-05T10:00:00Z",
                "phone": "9876543215",
            }
        ),
        json.dumps(
            {
                "event_id": "e_prec_02",
                "event_type": "call.telephony_failed",
                "workflow_id": "motor_renewal",
                "call_id": "c_prec_1",
                "occurred_at": "2026-10-05T10:00:05Z",
                "phone": "9876543215",
                "payload": {"reason": "sip_503"},
            }
        ),
        # Call 2: terminal (not_interested is terminal=true, success=false)
        json.dumps(
            {
                "event_id": "e_prec_03",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c_prec_2",
                "occurred_at": "2026-10-05T11:00:00Z",
                "phone": "9876543215",
            }
        ),
        json.dumps(
            {
                "event_id": "e_prec_04",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "c_prec_2",
                "occurred_at": "2026-10-05T11:02:00Z",
                "phone": "9876543215",
                "payload": {"disposition_code": "not_interested"},
            }
        ),
    ]
    events_path.write_text("\n".join(lines), encoding="utf-8")
    ingest_events_jsonl(db_session, events_path, catalogue)

    call_facts = build_call_facts(db_session, catalogue)
    lead_facts = build_lead_facts(db_session, call_facts)

    assert lead_facts[0].bucket == "completed"
