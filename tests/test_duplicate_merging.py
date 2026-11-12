"""Tests for duplicate lead merging, batch_id preservation, and default application."""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from solution.ingestion.leads import ingest_lead_csv
from solution.models import Lead
from solution.schemas import WorkflowSchema
from solution.security import compute_phone_hash, decrypt_string


def test_duplicate_lead_merging_and_permanent_batch_id(
    db_session: Session, motor_schema: WorkflowSchema, temp_dir: Path
):
    """Verify that duplicate rows preserve original batch_id and overwrite only non-null values."""
    # Batch 1: creates lead with batch_001
    batch1_path = temp_dir / "batch_001.csv"
    batch1_path.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        'Aarav Sharma,9876543210,MOT/2026/000001,2026-10-15,"₹12,000",hi\n',
        encoding="utf-8",
    )

    res1 = ingest_lead_csv(db_session, batch1_path, "motor_renewal", "batch_001", motor_schema)
    assert res1.lead_rows_total == 1
    assert res1.duplicate_lead_rows_merged == 0

    lead = db_session.scalar(select(Lead).where(Lead.workflow_id == "motor_renewal"))
    assert lead is not None
    assert lead.batch_id == "batch_001"
    assert decrypt_string(lead.encrypted_customer_name) == "Aarav Sharma"
    attrs1 = json.loads(lead.workflow_attributes or "{}")
    assert attrs1["preferred_language"] == "hi"
    assert attrs1["premium_amount"] == 12000.0

    # Batch 2: same lead (same canonical phone), with different batch_id, updated amount, but empty language
    batch2_path = temp_dir / "batch_002.csv"
    batch2_path.write_text(
        "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
        'Aarav S.,+91 98765 43210,MOT/2026/000001,2026-10-20,"₹15,000",\n',
        encoding="utf-8",
    )

    res2 = ingest_lead_csv(db_session, batch2_path, "motor_renewal", "batch_002", motor_schema)
    assert res2.lead_rows_total == 1
    assert res2.duplicate_lead_rows_merged == 1

    # Fetch updated lead
    db_session.expire_all()
    lead_after = db_session.scalar(select(Lead).where(Lead.workflow_id == "motor_renewal"))
    assert lead_after is not None
    # Crucial: batch_id MUST remain batch_001
    assert lead_after.batch_id == "batch_001"
    # customer_name overwritten
    assert decrypt_string(lead_after.encrypted_customer_name) == "Aarav S."
    # Attributes: premium_amount updated to 15000.0
    attrs2 = json.loads(lead_after.workflow_attributes or "{}")
    assert attrs2["premium_amount"] == 15000.0
    # preferred_language was empty in batch2 -> previous non-null value ('hi') must NOT be overwritten!
    assert attrs2["preferred_language"] == "hi"


def test_default_applied_after_merging(
    db_session: Session, motor_schema: WorkflowSchema, temp_dir: Path
):
    """Verify schema defaults apply to fields still null after merging."""
    csv_path = temp_dir / "batch_default.csv"
    # preferred_language left empty
    csv_path.write_text(
        "customer_name,phone,policy_number,preferred_language\n"
        "Rohan Das,9876543211,MOT/2026/000002,\n",
        encoding="utf-8",
    )

    res = ingest_lead_csv(db_session, csv_path, "motor_renewal", "batch_default", motor_schema)
    assert res.lead_rows_total == 1

    lead = db_session.scalar(
        select(Lead).where(Lead.phone_hash == compute_phone_hash("+919876543211"))
    )
    assert lead is not None
    attrs = json.loads(lead.workflow_attributes or "{}")
    # Schema default for preferred_language is 'en'
    assert attrs["preferred_language"] == "en"
