"""Tests verifying PII encryption at rest and deterministic HMAC-SHA256 phone matching."""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from solution.ingestion.leads import ingest_lead_csv
from solution.models import Lead, RejectedLead
from solution.schemas import WorkflowSchema
from solution.security import (
    compute_phone_hash,
    decrypt_string,
    encrypt_string,
)


def test_encryption_roundtrip():
    """Verify AES-256-GCM encrypt and decrypt roundtrip."""
    original = "Aarav Sharma"
    encrypted = encrypt_string(original)
    # Ciphertext must differ from plaintext
    assert original not in encrypted
    assert decrypt_string(encrypted) == original


def test_encryption_randomised_nonces():
    """Verify that encrypting the same text twice yields different ciphertexts due to randomised 12-byte nonces."""
    original = "+919876543210"
    ct1 = encrypt_string(original)
    ct2 = encrypt_string(original)
    assert ct1 != ct2
    assert decrypt_string(ct1) == original
    assert decrypt_string(ct2) == original


def test_deterministic_phone_hmac():
    """Verify HMAC-SHA256 is deterministic for matching."""
    phone = "+919876543210"
    h1 = compute_phone_hash(phone)
    h2 = compute_phone_hash(phone)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex string


def test_no_plaintext_pii_in_database(
    db_session: Session, motor_schema: WorkflowSchema, temp_dir: Path
):
    """Verify that plaintext customer name and phone do NOT appear in the database."""
    lead_file = temp_dir / "secure_batch.csv"
    lead_file.write_text(
        "customer_name,phone,policy_number\nConfidential Customer,9876543210,MOT/2026/999\n",
        encoding="utf-8",
    )
    ingest_lead_csv(db_session, lead_file, "motor_renewal", "batch_sec", motor_schema)

    lead = db_session.scalar(select(Lead).where(Lead.workflow_id == "motor_renewal"))
    assert lead is not None

    # Check database columns directly
    assert "Confidential Customer" not in lead.encrypted_customer_name
    assert "9876543210" not in lead.encrypted_canonical_phone
    assert "+919876543210" not in lead.encrypted_canonical_phone

    # Decryption works properly
    assert decrypt_string(lead.encrypted_customer_name) == "Confidential Customer"
    assert decrypt_string(lead.encrypted_canonical_phone) == "+919876543210"


def test_no_pii_in_rejection_logs(
    db_session: Session, motor_schema: WorkflowSchema, temp_dir: Path
):
    """Verify that rejection table does NOT store PII."""
    lead_file = temp_dir / "rej_batch.csv"
    lead_file.write_text(
        "customer_name,phone,policy_number\nSecret Person,12345,MOT/2026/888\n",  # Bad phone
        encoding="utf-8",
    )
    ingest_lead_csv(db_session, lead_file, "motor_renewal", "batch_rej", motor_schema)

    rejection = db_session.scalar(select(RejectedLead))
    assert rejection is not None
    assert rejection.reason == "invalid_phone"
    assert "Secret Person" not in rejection.reason
    assert "12345" not in rejection.reason
