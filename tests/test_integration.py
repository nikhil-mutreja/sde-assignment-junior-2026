"""End-to-end integration test verifying multi-drop ingestion and report generation."""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from solution.ingestion.events import ingest_events_jsonl
from solution.ingestion.leads import ingest_lead_csv
from solution.reporting.reports import generate_all_reports
from solution.schemas import DispositionCatalogue, WorkflowSchema
from solution.services.data_quality import compute_data_quality_metrics
from solution.services.ledger import resolve_pending_events


def test_full_pipeline_multi_drop_integration(
    db_session: Session,
    schemas: dict[str, WorkflowSchema],
    catalogue: DispositionCatalogue,
    temp_dir: Path,
):
    """Full integration test simulating Drop 1 followed by Drop 2 and report generation."""
    # Setup Drop 1: Leads batch_001 for motor_renewal and events_0001
    drop1_dir = temp_dir / "drop_1"
    drop1_leads = drop1_dir / "leads" / "motor_renewal"
    drop1_events = drop1_dir / "events"
    drop1_leads.mkdir(parents=True, exist_ok=True)
    drop1_events.mkdir(parents=True, exist_ok=True)

    b1_file = drop1_leads / "batch_001.csv"
    b1_file.write_text(
        "customer_name,phone,policy_number,premium_amount,preferred_language\n"
        'Aarav Sharma,9876543210,MOT/2026/101,"₹12,000",hi\n'
        "Vivaan Gupta,9876543211,MOT/2026/102,15000,en\n",
        encoding="utf-8",
    )

    ev1_file = drop1_events / "events_0001.jsonl"
    lines1 = [
        # Call for Aarav (connected + renewed)
        json.dumps(
            {
                "event_id": "e_int_01",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c_int_01",
                "occurred_at": "2026-10-05T10:00:00Z",
                "phone": "9876543210",
            }
        ),
        json.dumps(
            {
                "event_id": "e_int_02",
                "event_type": "call.connected",
                "workflow_id": "motor_renewal",
                "call_id": "c_int_01",
                "occurred_at": "2026-10-05T10:00:15Z",
                "phone": "9876543210",
            }
        ),
        json.dumps(
            {
                "event_id": "e_int_03",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "c_int_01",
                "occurred_at": "2026-10-05T10:03:00Z",
                "phone": "9876543210",
                "payload": {"disposition_code": "renewed"},
            }
        ),
        # Call for a lead arriving in Drop 2 (orphan in Drop 1!)
        json.dumps(
            {
                "event_id": "e_int_04",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "c_int_02",
                "occurred_at": "2026-10-05T11:00:00Z",
                "phone": "9876543212",  # Not in Drop 1
            }
        ),
    ]
    ev1_file.write_text("\n".join(lines1), encoding="utf-8")

    # Ingest Drop 1
    ingest_lead_csv(db_session, b1_file, "motor_renewal", "batch_001", schemas["motor_renewal"])
    ingest_events_jsonl(db_session, ev1_file, catalogue)

    dq_drop1 = compute_data_quality_metrics(db_session)
    assert dq_drop1["lead_rows_total"] == 2
    assert dq_drop1["events_total"] == 4
    assert dq_drop1["orphan_events"] == 1  # 9876543212 is an orphan

    # Setup Drop 2: batch_002 with the missing lead and override
    drop2_dir = temp_dir / "drop_2"
    drop2_leads = drop2_dir / "leads" / "motor_renewal"
    drop2_events = drop2_dir / "events"
    drop2_leads.mkdir(parents=True, exist_ok=True)
    drop2_events.mkdir(parents=True, exist_ok=True)

    b2_file = drop2_leads / "batch_002.csv"
    b2_file.write_text(
        "customer_name,phone,policy_number,preferred_language\n"
        "Diya Reddy,9876543212,MOT/2026/103,ta\n",
        encoding="utf-8",
    )

    ev2_file = drop2_events / "events_1001.jsonl"
    lines2 = [
        # Override for Aarav's call c_int_01 (has no phone)
        json.dumps(
            {
                "event_id": "e_int_05",
                "event_type": "disposition.override",
                "workflow_id": "motor_renewal",
                "call_id": "c_int_01",
                "occurred_at": "2026-10-06T09:00:00Z",
                "payload": {
                    "disposition_code": "payment_link_sent",
                    "overridden_by": "sup_1",
                    "reason": "qa_review",
                },
            }
        ),
    ]
    ev2_file.write_text("\n".join(lines2), encoding="utf-8")

    # Ingest Drop 2
    ingest_lead_csv(db_session, b2_file, "motor_renewal", "batch_002", schemas["motor_renewal"])
    resolve_pending_events(db_session, catalogue)
    ingest_events_jsonl(db_session, ev2_file, catalogue)
    resolve_pending_events(db_session, catalogue)

    dq_drop2 = compute_data_quality_metrics(db_session)
    assert dq_drop2["lead_rows_total"] == 3
    assert dq_drop2["events_total"] == 5
    assert dq_drop2["orphan_events"] == 0  # Orphan was resolved!

    # Generate reports
    reports_dir = temp_dir / "reports"
    generate_all_reports(db_session, catalogue, reports_dir)

    assert (reports_dir / "batch_summary.csv").exists()
    assert (reports_dir / "daily_overview.csv").exists()
    assert (reports_dir / "lead_report.csv").exists()
    assert (reports_dir / "rejections.csv").exists()
    assert (reports_dir / "data_quality.json").exists()
