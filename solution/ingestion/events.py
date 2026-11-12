"""Event JSONL file ingestion, deduplication, schema validation, and ledger persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from solution.models import IngestedFile, InteractionEvent, Lead
from solution.schemas import DispositionCatalogue
from solution.security import compute_phone_hash, encrypt_string
from solution.services.ledger import resolve_pending_events
from solution.utils.dates import parse_timestamp
from solution.utils.files import compute_file_sha256
from solution.utils.phones import normalise_phone


@dataclass
class EventIngestionResult:
    """Metrics produced during event JSONL ingestion."""

    file_path: str
    file_hash: str
    events_total: int = 0
    malformed_events: int = 0
    duplicate_events_dropped: int = 0
    unknown_disposition_events: int = 0
    skipped_as_duplicate: bool = False


def ingest_events_jsonl(
    session: Session,
    file_path: Path,
    catalogue: DispositionCatalogue,
) -> EventIngestionResult:
    """Ingest a single JSON Lines event file into the append-only ledger.

    Processing precedence per line:
    1. Malformed: not valid JSON, not a dictionary, or missing event_id -> count and skip.
    2. Duplicate: event_id already seen in this or prior drops -> count and skip.
    3. Linking & Orphan: attempt resolution via (workflow_id, normalised phone) or call_id.
    4. Unknown disposition: invalid disposition code on linked event -> count and flag.

    All valid events are persisted to the append-only ledger.
    """
    file_hash = compute_file_sha256(file_path)
    file_path_str = str(file_path.resolve())

    # Check file-level idempotency
    existing_file = session.scalar(
        select(IngestedFile).where(
            IngestedFile.file_path == file_path_str,
            IngestedFile.file_hash == file_hash,
        )
    )
    if existing_file:
        return EventIngestionResult(
            file_path=file_path_str,
            file_hash=file_hash,
            events_total=existing_file.events_total,
            malformed_events=existing_file.malformed_events,
            duplicate_events_dropped=existing_file.duplicate_events_dropped,
            skipped_as_duplicate=True,
        )

    result = EventIngestionResult(file_path=file_path_str, file_hash=file_hash)
    now_utc = datetime.now(timezone.utc)

    # In-memory lookup tables for high-performance deduplication and linking
    existing_event_ids: Set[str] = set(session.scalars(select(InteractionEvent.event_id)).all())

    lead_lookup: dict[tuple[str, str], int] = {}
    for l_wf, l_hash, l_id in session.execute(
        select(Lead.workflow_id, Lead.phone_hash, Lead.id)
    ).all():
        lead_lookup[(l_wf, l_hash)] = l_id

    call_lead_lookup: dict[str, int] = {}
    for c_id, l_id in session.execute(
        select(InteractionEvent.call_id, InteractionEvent.lead_id).where(
            InteractionEvent.lead_id.isnot(None),
            InteractionEvent.is_linked.is_(True),
        )
    ).all():
        call_lead_lookup[c_id] = l_id

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue

            result.events_total += 1

            # Step 1: Malformed check
            try:
                data = json.loads(line_str)
                if not isinstance(data, dict):
                    result.malformed_events += 1
                    continue
                event_id = data.get("event_id")
                if not event_id or not isinstance(event_id, str) or not event_id.strip():
                    result.malformed_events += 1
                    continue
                event_id = event_id.strip()
            except Exception:
                result.malformed_events += 1
                continue

            # Step 2: Duplicate event check
            if event_id in existing_event_ids:
                result.duplicate_events_dropped += 1
                continue

            existing_event_ids.add(event_id)

            # Extract fields
            event_type = str(data.get("event_type", "")).strip()
            workflow_id = str(data.get("workflow_id", "")).strip()
            call_id = str(data.get("call_id", "")).strip()
            raw_ts = str(data.get("occurred_at", "")).strip()
            payload = data.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}

            try:
                occurred_at = parse_timestamp(raw_ts)
            except Exception:
                # If timestamp is unparseable, count as malformed
                result.malformed_events += 1
                continue

            raw_phone = data.get("phone")
            canonical_phone = normalise_phone(raw_phone) if raw_phone else None
            phone_hash = compute_phone_hash(canonical_phone) if canonical_phone else None
            encrypted_phone = encrypt_string(canonical_phone) if canonical_phone else None

            # Step 3: Linking
            lead_id: Optional[int] = None
            is_linked = False

            if phone_hash:
                matched_id = lead_lookup.get((workflow_id, phone_hash))
                if matched_id is not None:
                    lead_id = matched_id
                    is_linked = True
                    call_lead_lookup[call_id] = matched_id
            else:
                # E.g. disposition.override: link via call_id to existing call event
                sibling_lead_id = call_lead_lookup.get(call_id)
                if sibling_lead_id is not None:
                    lead_id = sibling_lead_id
                    is_linked = True

            # Step 4: Unknown disposition check (evaluated on linked events per precedence)
            is_unknown_disp = False
            if is_linked and event_type.startswith("disposition."):
                disp_code = str(payload.get("disposition_code", "")).strip()
                if not catalogue.is_valid(workflow_id, disp_code):
                    is_unknown_disp = True
                    result.unknown_disposition_events += 1

            new_event = InteractionEvent(
                event_id=event_id,
                event_type=event_type,
                workflow_id=workflow_id,
                call_id=call_id,
                phone_hash=phone_hash,
                lead_id=lead_id,
                occurred_at=occurred_at,
                encrypted_phone=encrypted_phone,
                payload_json=json.dumps(payload),
                is_linked=is_linked,
                is_unknown_disposition=is_unknown_disp,
                created_at=now_utc,
            )
            session.add(new_event)

    # Resolve any pending/sibling orphans in the database
    resolve_pending_events(session, catalogue)

    # Record file ingestion for idempotency
    record = IngestedFile(
        file_path=file_path_str,
        file_hash=file_hash,
        file_type="events",
        ingested_at=now_utc,
        lead_rows_total=0,
        lead_rows_rejected=0,
        duplicate_lead_rows_merged=0,
        field_warnings=0,
        events_total=result.events_total,
        malformed_events=result.malformed_events,
        duplicate_events_dropped=result.duplicate_events_dropped,
    )
    session.add(record)
    session.flush()

    return result
