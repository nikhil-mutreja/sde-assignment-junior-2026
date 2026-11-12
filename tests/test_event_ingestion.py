"""Tests for event ingestion, deduplication, and append-only ledger semantics."""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from solution.ingestion.events import ingest_events_jsonl
from solution.models import InteractionEvent
from solution.schemas import DispositionCatalogue


def test_malformed_and_duplicate_event_handling(
    db_session: Session, catalogue: DispositionCatalogue, temp_dir: Path
):
    """Verify malformed JSON lines and duplicate event_ids are properly counted and skipped."""
    jsonl_path = temp_dir / "events.jsonl"
    lines = [
        "not a json line",  # Malformed 1
        json.dumps({"event_type": "call.initiated"}),  # Malformed 2 (no event_id)
        json.dumps(
            {  # Valid event 1
                "event_id": "evt_001",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_001",
                "occurred_at": "2026-10-05T13:20:21Z",
                "phone": "9876543210",
            }
        ),
        json.dumps(
            {  # Duplicate of evt_001
                "event_id": "evt_001",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_001",
                "occurred_at": "2026-10-05T13:20:21Z",
                "phone": "9876543210",
            }
        ),
        "",  # Empty line (ignored)
    ]
    jsonl_path.write_text("\n".join(lines), encoding="utf-8")

    res = ingest_events_jsonl(db_session, jsonl_path, catalogue)
    assert res.events_total == 4  # 4 non-empty lines
    assert res.malformed_events == 2
    assert res.duplicate_events_dropped == 1

    events = db_session.scalars(select(InteractionEvent)).all()
    assert len(events) == 1
    assert events[0].event_id == "evt_001"


def test_append_only_ledger_integrity(
    db_session: Session, catalogue: DispositionCatalogue, temp_dir: Path
):
    """Verify that interaction events are strictly append-only and overrides do not alter original events."""
    jsonl_path = temp_dir / "events_override.jsonl"
    lines = [
        json.dumps(
            {
                "event_id": "evt_001",
                "event_type": "disposition.set",
                "workflow_id": "motor_renewal",
                "call_id": "call_001",
                "occurred_at": "2026-10-05T13:25:00Z",
                "phone": "9876543210",
                "payload": {"disposition_code": "callback_requested"},
            }
        ),
        json.dumps(
            {
                "event_id": "evt_002",
                "event_type": "disposition.override",
                "workflow_id": "motor_renewal",
                "call_id": "call_001",
                "occurred_at": "2026-10-05T15:00:00Z",
                "payload": {"disposition_code": "renewed", "reason": "qa_review"},
            }
        ),
    ]
    jsonl_path.write_text("\n".join(lines), encoding="utf-8")

    ingest_events_jsonl(db_session, jsonl_path, catalogue)

    events = db_session.scalars(
        select(InteractionEvent).order_by(InteractionEvent.occurred_at)
    ).all()
    assert len(events) == 2
    # Original event is preserved untouched
    assert events[0].event_type == "disposition.set"
    p1 = json.loads(events[0].payload_json)
    assert p1["disposition_code"] == "callback_requested"
    # Override event is a new row
    assert events[1].event_type == "disposition.override"
    p2 = json.loads(events[1].payload_json)
    assert p2["disposition_code"] == "renewed"
