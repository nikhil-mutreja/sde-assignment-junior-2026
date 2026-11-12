"""Tests verifying exact headers, formatting, sorting, and output contracts for all five reports."""

import csv
import json
from pathlib import Path

from sqlalchemy.orm import Session

from solution.ingestion.events import ingest_events_jsonl
from solution.ingestion.leads import ingest_lead_csv
from solution.reporting.reports import generate_all_reports
from solution.schemas import DispositionCatalogue, WorkflowSchema


def test_reporting_exact_output_contracts(
    db_session: Session,
    motor_schema: WorkflowSchema,
    catalogue: DispositionCatalogue,
    temp_dir: Path,
):
    """Verify that all five reports match their documented contracts exactly."""
    # Create sample lead with a rejection row
    leads_path = temp_dir / "batch_001.csv"
    leads_path.write_text(
        "customer_name,phone,policy_number\n"
        "Aarav Sharma,9876543210,MOT/2026/001\n"
        ",9876543211,MOT/2026/002\n",  # Rejected row (no customer_name)
        encoding="utf-8",
    )
    ingest_lead_csv(db_session, leads_path, "motor_renewal", "batch_001", motor_schema)

    # Create sample events: call.initiated + call.connected + disposition.set(renewed)
    events_path = temp_dir / "events.jsonl"
    lines = [
        json.dumps(
            {
                "event_id": "evt_rep_01",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_rep_01",
                "occurred_at": "2026-10-05T10:00:00Z",
                "phone": "9876543210",
            }
        ),
        json.dumps(
            {
                "event_id": "evt_rep_02",
                "event_type": "call.connected",
                "workflow_id": "motor_renewal",
                "call_id": "call_rep_01",
                "occurred_at": "2026-10-05T10:00:10Z",
                "phone": "9876543210",
            }
        ),
        json.dumps(
            {
                "event_id": "evt_rep_03",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "call_rep_01",
                "occurred_at": "2026-10-05T10:02:00Z",
                "phone": "9876543210",
                "payload": {"disposition_code": "renewed"},
            }
        ),
    ]
    events_path.write_text("\n".join(lines), encoding="utf-8")
    ingest_events_jsonl(db_session, events_path, catalogue)

    reports_dir = temp_dir / "reports"
    generate_all_reports(db_session, catalogue, reports_dir)

    # 1. batch_summary.csv
    batch_csv = reports_dir / "batch_summary.csv"
    assert batch_csv.exists()
    with open(batch_csv, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert reader[0] == [
            "workflow_id",
            "batch_id",
            "total_leads",
            "yet_to_reach_out",
            "in_progress_telephony_failed",
            "in_progress_dialed",
            "completed",
        ]
        assert reader[1] == ["motor_renewal", "batch_001", "1", "0", "0", "0", "1"]

    # 2. daily_overview.csv
    daily_csv = reports_dir / "daily_overview.csv"
    assert daily_csv.exists()
    with open(daily_csv, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert reader[0] == [
            "workflow_id",
            "date",
            "total_calls",
            "connected_calls",
            "connectivity_rate",
            "success_leads",
        ]
        # 10:00:00 UTC = 15:30:00 IST on 2026-10-05
        # 1 connected / 1 total = 1.0000 exactly 4 decimals
        assert reader[1] == ["motor_renewal", "2026-10-05", "1", "1", "1.0000", "1"]

    # 3. lead_report.csv
    lead_csv = reports_dir / "lead_report.csv"
    assert lead_csv.exists()
    with open(lead_csv, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert reader[0] == [
            "workflow_id",
            "external_ref",
            "batch_id",
            "phone_masked",
            "bucket",
            "total_calls",
            "connected_calls",
            "last_call_at",
            "current_disposition",
            "ever_success",
        ]
        # Phone must be masked: +91******3210
        # last_call_at formatted as ISO-8601 in IST
        assert reader[1][0] == "motor_renewal"
        assert reader[1][1] == "MOT/2026/001"
        assert reader[1][2] == "batch_001"
        assert reader[1][3] == "+91******3210"
        assert reader[1][4] == "completed"
        assert reader[1][5] == "1"
        assert reader[1][6] == "1"
        assert "+05:30" in reader[1][7]
        assert reader[1][8] == "renewed"
        assert reader[1][9] == "true"

    # 4. rejections.csv
    rej_csv = reports_dir / "rejections.csv"
    assert rej_csv.exists()
    with open(rej_csv, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert reader[0] == ["workflow_id", "batch_id", "row_number", "reason"]
        assert reader[1] == ["motor_renewal", "batch_001", "2", "missing_required:customer_name"]

    # 5. data_quality.json
    dq_json = reports_dir / "data_quality.json"
    assert dq_json.exists()
    with open(dq_json, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["lead_rows_total"] == 2
        assert data["lead_rows_rejected"] == 1
        assert data["rejections_by_reason"] == {"missing_required:customer_name": 1}
        assert data["events_total"] == 3
        assert data["orphan_events"] == 0
