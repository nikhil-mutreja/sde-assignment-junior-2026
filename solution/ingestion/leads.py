"""Lead CSV file ingestion, header mapping, validation, and duplicate merging."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from solution.ingestion.validation import validate_lead_row
from solution.models import IngestedFile, Lead, RejectedLead
from solution.schemas import WorkflowSchema
from solution.security import compute_phone_hash, encrypt_string
from solution.utils.files import compute_file_sha256


@dataclass
class LeadIngestionResult:
    """Metrics produced during lead CSV ingestion."""

    file_path: str
    file_hash: str
    workflow_id: str
    batch_id: str
    lead_rows_total: int = 0
    lead_rows_rejected: int = 0
    duplicate_lead_rows_merged: int = 0
    field_warnings: int = 0
    rejections_by_reason: Dict[str, int] = field(default_factory=dict)
    skipped_as_duplicate: bool = False


def ingest_lead_csv(
    session: Session,
    file_path: Path,
    workflow_id: str,
    batch_id: str,
    schema: WorkflowSchema,
) -> LeadIngestionResult:
    """Ingest a single CSV lead batch file.

    Adheres strictly to the specification:
    - Header mapping with aliases and normalisation.
    - Skipping completely blank rows (not counted).
    - Row numbering non-blank data rows starting at 1.
    - Schema order validation with first failure rejection.
    - Deterministic phone hashing and AES-256-GCM encryption of PII.
    - Duplicate lead merging: permanent batch_id from first drop/file,
      overwriting only non-null fields, defaults applied to remaining nulls.
    - File-level idempotency tracking.
    """
    file_hash = compute_file_sha256(file_path)
    file_path_str = str(file_path.resolve())

    # Check if already ingested with identical content hash
    stmt = select(IngestedFile).where(
        IngestedFile.file_path == file_path_str,
        IngestedFile.file_hash == file_hash,
    )
    existing_file = session.scalar(stmt)
    if existing_file:
        return LeadIngestionResult(
            file_path=file_path_str,
            file_hash=file_hash,
            workflow_id=workflow_id,
            batch_id=batch_id,
            lead_rows_total=existing_file.lead_rows_total,
            lead_rows_rejected=existing_file.lead_rows_rejected,
            duplicate_lead_rows_merged=existing_file.duplicate_lead_rows_merged,
            field_warnings=existing_file.field_warnings,
            skipped_as_duplicate=True,
        )

    ref_field = schema.get_reference_field().name
    now_utc = datetime.now(timezone.utc)

    result = LeadIngestionResult(
        file_path=file_path_str,
        file_hash=file_hash,
        workflow_id=workflow_id,
        batch_id=batch_id,
    )

    # Pre-populate leads cache for this workflow to handle intra-file and inter-file duplicates
    existing_leads = session.scalars(select(Lead).where(Lead.workflow_id == workflow_id)).all()
    leads_cache: Dict[str, Lead] = {lead.phone_hash: lead for lead in existing_leads}

    with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header_row: Optional[List[str]] = None
        for row in reader:
            if any(cell.strip() for cell in row):
                header_row = row
                break

        if header_row is None:
            # Entirely empty file
            return result

        header_mapping = schema.map_headers(header_row)

        data_row_idx = 0
        for raw_row in reader:
            # Check for completely blank row (every cell empty or whitespace)
            if not any(cell.strip() for cell in raw_row):
                continue

            data_row_idx += 1
            result.lead_rows_total += 1

            # Build row dict from matched headers
            row_dict: Dict[str, str] = {}
            for col_idx, cell_value in enumerate(raw_row):
                if col_idx < len(header_row):
                    raw_h = header_row[col_idx]
                    if raw_h in header_mapping:
                        schema_field = header_mapping[raw_h]
                        row_dict[schema_field] = cell_value.strip()

            lead_data, rejection_reason, warnings = validate_lead_row(schema, row_dict)

            if rejection_reason:
                result.lead_rows_rejected += 1
                result.rejections_by_reason[rejection_reason] = (
                    result.rejections_by_reason.get(rejection_reason, 0) + 1
                )
                rejection_entry = RejectedLead(
                    workflow_id=workflow_id,
                    batch_id=batch_id,
                    row_number=data_row_idx,
                    reason=rejection_reason,
                )
                session.add(rejection_entry)
                continue

            # Valid lead row
            assert lead_data is not None
            result.field_warnings += warnings

            canonical_phone = lead_data["phone"]
            phone_hash = compute_phone_hash(canonical_phone)
            cust_name = lead_data["customer_name"]
            external_ref = str(lead_data[ref_field])

            # Check if lead already exists via in-memory cache
            existing_lead = leads_cache.get(phone_hash)

            # Separate non-PII attributes vs PII
            attrs: Dict[str, Any] = {}
            for f_def in schema.fields:
                if f_def.name not in ("customer_name", "phone") and not f_def.pii:
                    attrs[f_def.name] = lead_data.get(f_def.name)

            if existing_lead is None:
                # Apply defaults for null fields
                for f_def in schema.fields:
                    if f_def.default and attrs.get(f_def.name) is None:
                        attrs[f_def.name] = f_def.default

                new_lead = Lead(
                    workflow_id=workflow_id,
                    phone_hash=phone_hash,
                    batch_id=batch_id,  # First valid batch permanently established
                    external_ref=external_ref,
                    phone_last4=canonical_phone[-4:],
                    encrypted_customer_name=encrypt_string(cust_name),
                    encrypted_canonical_phone=encrypt_string(canonical_phone),
                    encrypted_pii_fields=None,
                    workflow_attributes=json.dumps(attrs),
                    created_at=now_utc,
                    updated_at=now_utc,
                )
                session.add(new_lead)
                leads_cache[phone_hash] = new_lead
            else:
                # Merge duplicate: batch_id permanently retained
                result.duplicate_lead_rows_merged += 1

                # Overwrite customer_name if non-null in new row
                if cust_name is not None:
                    existing_lead.encrypted_customer_name = encrypt_string(cust_name)

                # Overwrite external_ref if non-null
                if lead_data.get(ref_field) is not None:
                    existing_lead.external_ref = external_ref

                # Merge non-PII attributes (last write wins for non-null values)
                curr_attrs = json.loads(existing_lead.workflow_attributes or "{}")
                for k, v in attrs.items():
                    if v is not None:
                        curr_attrs[k] = v

                # Apply defaults for fields still null
                for f_def in schema.fields:
                    if f_def.default and curr_attrs.get(f_def.name) is None:
                        curr_attrs[f_def.name] = f_def.default

                existing_lead.workflow_attributes = json.dumps(curr_attrs)
                existing_lead.updated_at = now_utc

    # Record file ingestion for idempotency
    record = IngestedFile(
        file_path=file_path_str,
        file_hash=file_hash,
        file_type="leads",
        workflow_id=workflow_id,
        batch_id=batch_id,
        ingested_at=now_utc,
        lead_rows_total=result.lead_rows_total,
        lead_rows_rejected=result.lead_rows_rejected,
        duplicate_lead_rows_merged=result.duplicate_lead_rows_merged,
        field_warnings=result.field_warnings,
        events_total=0,
        malformed_events=0,
        duplicate_events_dropped=0,
    )
    session.add(record)
    session.flush()

    return result
