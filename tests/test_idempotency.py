"""Tests for idempotency, incremental drop processing, and rerun safety."""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from solution.ingestion.events import ingest_events_jsonl
from solution.ingestion.leads import ingest_lead_csv
from solution.models import InteractionEvent, Lead, RejectedLead
from solution.schemas import DispositionCatalogue, WorkflowSchema
from solution.services.data_quality import compute_data_quality_metrics


def test_idempotent_reingestion_no_duplicate_records_or_inflated_counters(
    db_session: Session,
    motor_schema: WorkflowSchema,
    catalogue: DispositionCatalogue,
    temp_dir: Path,
):
    """Verify that re-ingesting the exact same lead and event files produces no extra rows and no inflated counters."""
    lead_file = temp_dir / "batch_001.csv"
    lead_file.write_text(
        "customer_name,phone,policy_number\n"
        "Aarav Sharma,9876543210,MOT/2026/001\n"
        ",9876543211,MOT/2026/002\n",  # 1 valid, 1 rejected
        encoding="utf-8",
    )

    event_file = temp_dir / "events.jsonl"
    event_file.write_text(
        json.dumps(
            {
                "event_id": "evt_idem_01",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_idem_01",
                "occurred_at": "2026-10-05T10:00:00Z",
                "phone": "9876543210",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # First ingestion run
    res_l1 = ingest_lead_csv(db_session, lead_file, "motor_renewal", "batch_001", motor_schema)
    res_e1 = ingest_events_jsonl(db_session, event_file, catalogue)
    assert res_l1.skipped_as_duplicate is False
    assert res_e1.skipped_as_duplicate is False

    dq1 = compute_data_quality_metrics(db_session)
    leads_count1 = db_session.scalar(select(func.count(Lead.id)))
    events_count1 = db_session.scalar(select(func.count(InteractionEvent.id)))
    rej_count1 = db_session.scalar(select(func.count(RejectedLead.id)))

    # Second ingestion run (replay of identical files)
    res_l2 = ingest_lead_csv(db_session, lead_file, "motor_renewal", "batch_001", motor_schema)
    res_e2 = ingest_events_jsonl(db_session, event_file, catalogue)
    assert res_l2.skipped_as_duplicate is True
    assert res_e2.skipped_as_duplicate is True

    dq2 = compute_data_quality_metrics(db_session)
    leads_count2 = db_session.scalar(select(func.count(Lead.id)))
    events_count2 = db_session.scalar(select(func.count(InteractionEvent.id)))
    rej_count2 = db_session.scalar(select(func.count(RejectedLead.id)))

    # Exact database state and metrics must be identical
    assert leads_count1 == leads_count2 == 1
    assert events_count1 == events_count2 == 1
    assert rej_count1 == rej_count2 == 1

    assert dq1 == dq2
    assert dq2["lead_rows_total"] == 2
    assert dq2["events_total"] == 1
